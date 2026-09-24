"""Phase 5 (SMS, notifications, calendar) and 5b (BYO SIP) — offline/simulated providers only.

Nothing here talks to Telnyx, LiveKit or a registrar; live PSTN/SIP validation is a separate
manual step once the UK number leaves `requirement-info-pending`.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from itertools import pairwise
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.calendar import (
    Booking,
    BookingRules,
    CalendarConnection,
    CalendarProvider,
    ConnectionStatus,
    ServiceType,
    Slot,
    free_slots,
    slot_allowed,
)
from parlio_api.messaging import (
    LogSmsProvider,
    MessageService,
    MessageStatus,
    render_template,
)
from parlio_api.notifications import (
    Channel,
    NotificationEvent,
    NotificationRule,
    NotifyEvent,
    is_qualified_lead,
)
from parlio_api.sip import (
    ConcurrencyGuard,
    DdiRoute,
    RegistrationState,
    RouteWhen,
    SipTrunk,
    TrunkInput,
    TrunkMode,
    route_decision,
)
from parlio_api.store import CallRecord, MemoryStore
from parlio_api.vault import LocalVault, is_sealed
from parlio_voice.models import (
    AssistantConfig,
    CallEventType,
    DayHours,
    Schedule,
    SmsScenario,
    SmsTrigger,
)

from .test_api import HEADERS, ev


def cfg(**over: Any) -> AssistantConfig:
    return AssistantConfig(
        tenant_id="demo",
        company_id="demo",
        assistant_id="demo",
        name="Ava",
        business_name="Acme Plumbing",
        sms_scenarios=[
            SmsScenario(
                trigger=SmsTrigger.AFTER_CALL,
                name="Thanks",
                template="Thanks for calling {business_name}, {caller_name}.",
            ),
            SmsScenario(
                trigger=SmsTrigger.TICKET_CONFIRMATION,
                name="Ticket",
                template="Your ticket {ticket_id} is logged.",
            ),
            SmsScenario(
                trigger=SmsTrigger.BOOKING_LINK,
                name="Book",
                template="Book here: https://cal.com/acme",
                enabled=False,
            ),
        ],
        **over,
    )


# -- vault ---------------------------------------------------------------------------------------


def test_vault_roundtrip_and_key_mismatch() -> None:
    v = LocalVault("k1")
    sealed = v.seal("s3cret")
    assert is_sealed(sealed) and "s3cret" not in sealed
    assert v.open(sealed) == "s3cret"
    with pytest.raises(ValueError):
        LocalVault("k2").open(sealed)
    with pytest.raises(ValueError):
        v.open("plain")


# -- messaging -----------------------------------------------------------------------------------


def test_render_template_missing_keys_are_blanked() -> None:
    out = render_template("Hi {caller_name} from {business_name} {nope}", {"business_name": "X"})
    assert out == "Hi from X"


async def test_sms_scenarios_send_skip_and_fail() -> None:
    store = MemoryStore(None)
    prov = LogSmsProvider(fail_numbers={"+447700900999"})
    svc = MessageService(store, prov, "+442046206823")
    c = cfg()

    m = await svc.send_scenario(
        c, SmsTrigger.AFTER_CALL, "+447700900001", {"caller_name": "Sam"}, call_id="c1"
    )
    assert m is not None and m.status == MessageStatus.SENT
    assert prov.sent[-1] == (
        "+442046206823",
        "+447700900001",
        "Thanks for calling Acme Plumbing, Sam.",
    )

    # duplicate suppression per call+trigger
    again = await svc.send_scenario(c, SmsTrigger.AFTER_CALL, "+447700900001", {}, call_id="c1")
    assert again is None and len(prov.sent) == 1

    # disabled scenario -> nothing sent
    assert await svc.send_scenario(c, SmsTrigger.BOOKING_LINK, "+447700900001", {}) is None

    # provider failure is recorded, not raised
    failed = await svc.send_scenario(c, SmsTrigger.TICKET_CONFIRMATION, "+447700900999", {})
    assert failed is not None and failed.status == MessageStatus.FAILED and failed.error

    # tenant isolation on listing
    assert len(await svc.recent("demo")) == 2
    assert await svc.recent("other") == []


async def test_after_call_sms_needs_caller_number() -> None:
    store = MemoryStore(None)
    prov = LogSmsProvider()
    svc = MessageService(store, prov, "+442046206823")
    call = CallRecord(call_id="x", tenant_id="demo", company_id="demo", assistant_id="demo")
    assert await svc.on_call_ended(call, cfg()) is None
    call.caller = "+447700900002"
    m = await svc.on_call_ended(call, cfg())
    assert m is None  # unanswered -> missed-call scenario, which this assistant lacks
    call.status = "completed"
    call.answered_at = datetime.now(UTC)
    m = await svc.on_call_ended(call, cfg())
    assert m is not None and m.trigger == SmsTrigger.AFTER_CALL


async def test_call_ended_event_triggers_sms_via_hub(client: AsyncClient, app: FastAPI) -> None:
    app.state.sms.from_number = "+442046206823"
    store = app.state.store
    c = await store.get_assistant("demo")
    assert c is not None
    c.sms_scenarios = cfg().sms_scenarios
    await store.upsert_assistant(c, [])
    await client.post(
        "/v1/worker/events",
        json=ev(CallEventType.CALL_STARTED, "call-sms", {"caller": "+447700900123"}),
        headers=HEADERS,
    )
    await client.post(
        "/v1/worker/events",
        json=ev(CallEventType.CALL_ANSWERED, "call-sms", {}),
        headers=HEADERS,
    )
    await client.post(
        "/v1/worker/events",
        json=ev(CallEventType.CALL_ENDED, "call-sms", {"duration_s": 40}),
        headers=HEADERS,
    )
    r = await client.get("/v1/messages", params={"tenant_id": "demo", "call_id": "call-sms"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1 and body[0]["trigger"] == "after_call" and body[0]["status"] == "sent"
    r = await client.get("/v1/messages", params={"tenant_id": "other"})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert r.json() == []


# -- notifications -------------------------------------------------------------------------------


def test_qualified_lead_filter() -> None:
    base = CallRecord(call_id="q", tenant_id="t", company_id="c", assistant_id="a")
    assert not is_qualified_lead(base)
    base.status = "completed"
    base.answered_at = datetime.now(UTC)
    base.duration_s = 90
    base.extracted = {"name": "Sam"}
    assert is_qualified_lead(base)
    failed = base.model_copy(update={"status": "failed"})
    assert not is_qualified_lead(failed)


def test_rule_matching() -> None:
    rule = NotificationRule(
        tenant_id="t",
        company_id="c",
        channel=Channel.WEBHOOK,
        target="https://example.test/hook",
        events=[NotifyEvent.CALL_COMPLETED],
        qualified_only=True,
        departments=["sales"],
    )
    ev1 = NotificationEvent(
        tenant_id="t", event=NotifyEvent.CALL_COMPLETED, title="x", body="y", qualified=True
    )
    assert not rule.matches(ev1)  # no department on event
    assert rule.matches(ev1.model_copy(update={"department": "Sales"}))
    assert not rule.matches(ev1.model_copy(update={"department": "sales", "qualified": False}))
    assert not rule.matches(ev1.model_copy(update={"event": NotifyEvent.TICKET_CREATED}))
    assert not rule.model_copy(update={"enabled": False}).matches(
        ev1.model_copy(update={"department": "sales"})
    )


async def test_notification_rules_api_and_test_send(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/notifications/rules",
        params={"tenant_id": "demo"},
        json={"channel": "email", "target": "ops@acme.test", "events": ["ticket.created"]},
    )
    assert r.status_code == 201, r.text
    rule = r.json()
    r = await client.post(
        f"/v1/notifications/rules/{rule['id']}/test", params={"tenant_id": "demo"}
    )
    assert r.status_code == 200, r.text
    r = await client.get("/v1/notifications/log", params={"tenant_id": "demo"})
    assert r.status_code == 200 and len(r.json()) >= 1
    assert r.json()[0]["rule_id"] == rule["id"]
    r = await client.get("/v1/notifications/rules", params={"tenant_id": "demo"})
    assert [x["id"] for x in r.json()] == [rule["id"]]


# -- calendar ------------------------------------------------------------------------------------


def test_free_slots_respects_hours_busy_and_buffer() -> None:
    conn = CalendarConnection(
        tenant_id="t",
        company_id="c",
        provider=CalendarProvider.SIMULATED,
        slot_minutes=30,
        buffer_minutes=10,
        hours=Schedule(hours={"mon": DayHours(open=time(9), close=time(12))}, timezone="UTC"),
    )
    monday = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
    busy = [Slot(start=monday.replace(hour=10), end=monday.replace(hour=10, minute=30))]
    slots = free_slots(conn, busy, monday, monday + timedelta(days=1), now=monday)
    starts = [s.start.hour * 60 + s.start.minute for s in slots]
    assert 9 * 60 in starts and 11 * 60 + 30 in starts
    # 9:30-10:00 collides with buffer around 10:00 booking; 10:00 and 10:30 are blocked too
    assert 9 * 60 + 30 not in starts and 10 * 60 not in starts and 10 * 60 + 30 not in starts
    assert all(s.end <= monday.replace(hour=12) for s in slots)


def _rules_conn(**rules: Any) -> CalendarConnection:
    return CalendarConnection(
        tenant_id="t",
        company_id="c",
        provider=CalendarProvider.SIMULATED,
        slot_minutes=60,
        buffer_minutes=30,
        hours=Schedule(
            hours={"mon": DayHours(open=time(8, 45), close=time(17, 0))}, timezone="UTC"
        ),
        rules=BookingRules(**rules),
    )


def test_booking_rules_grid_hours_close_and_travel_gap() -> None:
    """1 h slots on the hour/half-hour, inside hours, finishing by close, 30 min travel gap."""
    conn = _rules_conn(align_minutes=30)
    monday = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
    busy = [Slot(start=monday.replace(hour=11), end=monday.replace(hour=12))]
    slots = free_slots(conn, busy, monday, monday + timedelta(days=1), now=monday)
    starts = [(s.start.hour, s.start.minute) for s in slots]
    assert starts[0] == (9, 0)  # 08:45 open rounds up to the grid
    assert all(m in (0, 30) for _, m in starts)
    assert (9, 30) in starts and (10, 0) not in starts  # 10:00-11:00 + 30 min gap hits 11:00
    assert (12, 0) not in starts and (12, 30) in starts  # needs 30 min after the 12:00 finish
    assert (16, 0) in starts and (16, 30) not in starts  # 16:30 would finish after 17:00 close
    assert all(s.end <= monday.replace(hour=17) for s in slots)


def test_booking_rules_notice_window_and_hourly_grid() -> None:
    conn = _rules_conn(align_minutes=60, min_notice_minutes=120, max_days_ahead=1)
    monday = datetime(2026, 9, 14, 8, 10, tzinfo=UTC)
    slots = free_slots(conn, [], monday, monday + timedelta(days=14), now=monday)
    assert slots and slots[0].start == monday.replace(hour=11, minute=0)  # 10:10 + grid -> 11:00
    assert all(s.start.minute == 0 for s in slots)
    assert all(s.start < monday + timedelta(days=1) for s in slots)
    at = lambda h, m=0: monday.replace(hour=h, minute=m)  # noqa: E731
    assert slot_allowed(conn, at(9), now=monday) is not None  # within notice
    assert slot_allowed(conn, at(11, 30), now=monday) is not None  # off the hourly grid
    assert slot_allowed(conn, at(16, 30), now=monday) is not None  # would finish after close
    assert slot_allowed(conn, at(11), now=monday) is None
    tuesday = at(11) + timedelta(days=1)
    assert "days ahead" in (slot_allowed(conn, tuesday, now=monday) or "")
    conn.rules.max_days_ahead = 30
    assert slot_allowed(conn, tuesday, now=monday) == "we are closed that day"


def test_booking_rules_service_types_and_emergency_exception() -> None:
    service = ServiceType(name="Boiler service", minutes=90)
    emergency = ServiceType(name="Emergency call-out", minutes=60, emergency=True)
    conn = _rules_conn(align_minutes=30, services=[service, emergency])
    assert conn.rules.service("boiler service") is service
    assert conn.rules.service(emergency.id) is emergency
    assert conn.rules.service("nope") is None
    sunday = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)  # closed day, evening
    slots = free_slots(conn, [], sunday, sunday + timedelta(days=2), now=sunday, service=service)
    assert slots and all((s.end - s.start) == timedelta(minutes=90) for s in slots)
    assert slots[0].start.weekday() == 0 and all(s.end.hour <= 17 for s in slots)
    urgent = free_slots(
        conn, [], sunday, sunday + timedelta(days=2), now=sunday, service=emergency, limit=3
    )
    assert urgent and urgent[0].start == sunday  # tonight, outside hours
    assert slot_allowed(conn, sunday, now=sunday, service=emergency) is None
    assert slot_allowed(conn, sunday, now=sunday, service=service) == "we are closed that day"
    conn.rules.emergency_any_time = False
    assert slot_allowed(conn, sunday, now=sunday, service=emergency) == "we are closed that day"


async def test_booking_rules_api_and_service_aware_booking(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/calendar/connections",
        params={"tenant_id": "demo"},
        json={"provider": "simulated", "name": "Rules diary"},
    )
    assert r.status_code == 201, r.text
    conn_id = r.json()["id"]
    rules = {
        "slot_minutes": 60,
        "buffer_minutes": 30,
        "rules": {
            "align_minutes": 30,
            "use_business_hours": False,
            "min_notice_minutes": 60,
            "max_days_ahead": 14,
            "services": [
                {"name": "Repair", "minutes": 60},
                {"name": "Boiler service", "minutes": 90},
            ],
        },
    }
    r = await client.put(
        f"/v1/calendar/connections/{conn_id}/rules", params={"tenant_id": "demo"}, json=rules
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slot_minutes"] == 60 and body["rules"]["align_minutes"] == 30
    services = body["rules"]["services"]
    assert [s["name"] for s in services] == ["Repair", "Boiler service"]
    dup = {**rules, "rules": {**rules["rules"], "services": [services[0], services[0]]}}
    r = await client.put(
        f"/v1/calendar/connections/{conn_id}/rules", params={"tenant_id": "demo"}, json=dup
    )
    assert r.status_code == 400
    # other tenants cannot see or edit it
    r = await client.put(
        f"/v1/calendar/connections/{conn_id}/rules", params={"tenant_id": "other"}, json=rules
    )
    assert r.status_code in (403, 404)

    boiler = services[1]["id"]
    r = await client.get(
        "/v1/worker/calendar/availability",
        params={"tenant_id": "demo", "connection_id": conn_id, "service_id": boiler},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    avail = r.json()
    assert avail["service_id"] == boiler and avail["slot_minutes"] == 90
    assert [s["name"] for s in avail["services"]] == ["Repair", "Boiler service"]
    slots = avail["slots"]
    assert slots and all(datetime.fromisoformat(s["start"]).minute in (0, 30) for s in slots)
    start = datetime.fromisoformat(slots[0]["start"])
    assert datetime.fromisoformat(slots[0]["end"]) - start == timedelta(minutes=90)

    r = await client.get(
        "/v1/worker/calendar/availability",
        params={"tenant_id": "demo", "service_id": "massage"},
        headers=HEADERS,
    )
    assert r.status_code == 200 and "unknown service" in r.json()["error"]

    # off-grid bookings are refused before touching the calendar (pick a slot that is not the
    # last of its day so shifting it by 10 minutes still ends inside booking hours)
    starts = [datetime.fromisoformat(s["start"]) for s in slots]
    mid = next(a for a, b in pairwise(starts) if b - a == timedelta(minutes=30))
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params={"tenant_id": "demo"},
        json={
            "connection_id": conn_id,
            "start": (mid + timedelta(minutes=10)).isoformat(),
            "name": "Sam",
            "service_id": boiler,
        },
        headers=HEADERS,
    )
    assert r.status_code == 409, r.text
    assert "grid" in r.json()["detail"]
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params={"tenant_id": "demo"},
        json={
            "connection_id": conn_id,
            "start": slots[0]["start"],
            "name": "Sam",
            "service_id": "nope",
        },
        headers=HEADERS,
    )
    assert r.status_code == 409 and "unknown service" in r.json()["detail"]
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params={"tenant_id": "demo"},
        json={
            "connection_id": conn_id,
            "start": slots[0]["start"],
            "name": "Sam",
            "phone": "+447700900123",
            "service_id": "boiler service",
            "duration_minutes": 5,  # ignored: the service decides the length
        },
        headers=HEADERS,
    )
    assert r.status_code == 201, r.text
    booking = Booking.model_validate(r.json())
    assert booking.service_name == "Boiler service" and booking.end - booking.start == timedelta(
        minutes=90
    )
    # the 30 min travel gap now blocks the slot straight after the booking
    r = await client.get(
        "/v1/worker/calendar/availability",
        params={"tenant_id": "demo", "connection_id": conn_id, "service_id": boiler},
        headers=HEADERS,
    )
    later = [datetime.fromisoformat(s["start"]) for s in r.json()["slots"]]
    assert booking.start not in later and booking.end not in later
    assert all(t >= booking.end + timedelta(minutes=30) or t < booking.start for t in later)


async def test_calendar_connection_availability_and_booking(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/calendar/connections",
        params={"tenant_id": "demo"},
        json={"provider": "simulated", "name": "Test diary"},
    )
    assert r.status_code == 201, r.text
    conn = r.json()
    assert conn["status"] == ConnectionStatus.CONNECTED and "token_sealed" not in conn
    r = await client.get("/v1/calendar/availability", params={"tenant_id": "demo", "days": 7})
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    assert slots, "simulated diary should have free slots in the coming week"
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params={"tenant_id": "demo"},
        json={"start": slots[0]["start"], "name": "Sam", "phone": "+447700900123"},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.text
    booking = Booking.model_validate(r.json())
    assert booking.provider_ref
    r = await client.get("/v1/calendar/sync-log", params={"tenant_id": "demo"})
    assert r.status_code == 200 and any(e["ok"] for e in r.json())
    # booking-link vendors need no OAuth and are bookable via link only
    r = await client.post(
        "/v1/calendar/connections",
        params={"tenant_id": "demo"},
        json={"provider": "booking_link", "booking_url": "https://cal.com/acme", "name": "Cal"},
    )
    assert r.status_code == 201 and r.json()["bookable"] is False


# -- SIP -----------------------------------------------------------------------------------------


def test_trunk_validation() -> None:
    with pytest.raises(ValueError):
        SipTrunk(tenant_id="t", company_id="c", srtp=True)  # SRTP requires TLS
    with pytest.raises(ValueError):
        SipTrunk(tenant_id="t", company_id="c", mode=TrunkMode.BYO_REGISTER)
    ok = SipTrunk(
        tenant_id="t",
        company_id="c",
        mode=TrunkMode.BYO_REGISTER,
        registrar="sip.voipfone.net",
        username="123456",
    )
    assert ok.needs_registration


def test_route_decision_and_concurrency() -> None:
    trunk = SipTrunk(tenant_id="t", company_id="c", max_concurrent_calls=1)
    ddi = DdiRoute(e164="+442046206823", assistant_id="a", when=RouteWhen.OUT_OF_HOURS)
    hours = Schedule(hours={"mon": DayHours(open=time(9), close=time(17))}, timezone="UTC")
    in_hours = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)
    after = datetime(2026, 9, 14, 19, 0, tzinfo=UTC)
    assert route_decision(trunk, ddi, hours, in_hours) == (
        False,
        "staff hours: call left for the PBX/provider",
    )
    assert route_decision(trunk, ddi, hours, after) == (True, None)
    assert route_decision(trunk, ddi, None, in_hours) == (True, None)
    disabled = trunk.model_copy(update={"enabled": False})
    assert route_decision(disabled, ddi, None, in_hours)[0] is False

    g = ConcurrencyGuard()
    assert g.acquire(trunk.id, "c1", 1)
    assert g.acquire(trunk.id, "c1", 1)  # idempotent
    assert not g.acquire(trunk.id, "c2", 1)
    g.release("c1")
    assert g.acquire(trunk.id, "c2", 1)


async def test_trunk_lifecycle_api(client: AsyncClient, app: FastAPI) -> None:
    r = await client.get("/v1/telephony/guides")
    assert r.status_code == 200 and any(g["id"] == "voipfone" for g in r.json())

    # PBX mode: Parlio issues credentials, returned once, stored sealed
    r = await client.post(
        "/v1/telephony/trunks",
        params={"tenant_id": "demo"},
        json=TrunkInput(
            mode=TrunkMode.PBX,
            pbx_address="pbx.acme.test",
            ddis=[DdiRoute(e164="+442046206823", assistant_id="demo")],
            max_concurrent_calls=1,
        ).model_dump(mode="json"),
    )
    assert r.status_code == 201, r.text
    view = r.json()
    trunk_id = view["trunk"]["id"]
    assert view["credentials"] and view["credentials"]["password"]
    assert view["trunk"]["registration"]["state"] == RegistrationState.NOT_REQUIRED
    assert "password_sealed" not in view["trunk"] and "password" not in view["trunk"]
    doc = await app.state.store.get_doc("sip_trunk", trunk_id)
    assert doc is not None and is_sealed(doc.data["password_sealed"])
    assert view["credentials"]["password"] not in doc.data["password_sealed"]
    assert app.state.vault.open(doc.data["password_sealed"]) == view["credentials"]["password"]

    # worker admission: DDI routes to assistant, concurrency limit enforced, release frees
    r = await client.post(
        "/v1/worker/telephony/admit",
        params={"number": "+442046206823", "call_id": "k1"},
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json()["allowed"] and r.json()["assistant_id"] == "demo"
    r = await client.post(
        "/v1/worker/telephony/admit",
        params={"number": "+442046206823", "call_id": "k2"},
        headers=HEADERS,
    )
    assert r.json()["allowed"] is False and "limit" in r.json()["reason"]
    r = await client.post("/v1/worker/telephony/release", params={"call_id": "k1"}, headers=HEADERS)
    assert r.status_code == 204
    r = await client.post(
        "/v1/worker/telephony/admit",
        params={"number": "+442046206823", "call_id": "k2"},
        headers=HEADERS,
    )
    assert r.json()["allowed"] is True

    # test call is explicitly simulated (no live SIP/PSTN claim)
    r = await client.post(
        f"/v1/telephony/trunks/{trunk_id}/test-call",
        params={"tenant_id": "demo"},
        json={"to": "+447700900123"},
    )
    assert r.status_code == 200 and r.json()["simulated"] is True

    # BYO registration mode: customer password is write-only
    r = await client.put(
        f"/v1/telephony/trunks/{trunk_id}",
        params={"tenant_id": "demo"},
        json=TrunkInput(
            mode=TrunkMode.BYO_REGISTER,
            registrar="sip.voipfone.net",
            username="123456",
            password="hunter22",
            ddis=[DdiRoute(e164="+442046206823", assistant_id="demo")],
        ).model_dump(mode="json"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["credentials"] is None
    assert r.json()["trunk"]["registration"]["state"] == RegistrationState.PENDING
    doc = await app.state.store.get_doc("sip_trunk", trunk_id)
    assert doc is not None and app.state.vault.open(doc.data["password_sealed"]) == "hunter22"

    # tenant isolation
    r = await client.get("/v1/telephony/trunks", params={"tenant_id": "other"})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert r.json() == []
    r = await client.get("/v1/telephony/trunks", params={"tenant_id": "demo"})
    assert [t["id"] for t in r.json()] == [trunk_id]
