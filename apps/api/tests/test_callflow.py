"""Phase 20 call-flow features: owner SMS summary, SMS appointment reminders with reply
handling, call screening / spam filtering, and Google + Microsoft calendar regression."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.calendar import (
    Booking,
    BookingRequest,
    CalendarConnection,
    CalendarProvider,
    CalendarService,
    ConnectionStatus,
    GoogleCalendarBackend,
    MicrosoftCalendarBackend,
    Slot,
)
from parlio_api.messaging import LogSmsProvider, MessageService, MessageStatus
from parlio_api.notifications import owner_sms_summary
from parlio_api.reminders import (
    ReminderPolicy,
    ReminderService,
    ReminderStatus,
    interpret_reply,
)
from parlio_api.screening import (
    PLATFORM_TENANT,
    ScreeningAction,
    ScreeningService,
    looks_like_robocaller,
)
from parlio_api.store import CallRecord, ContactUpdate, MemoryStore
from parlio_api.vault import LocalVault
from parlio_voice.models import (
    AssistantConfig,
    CallEventType,
    ScreeningConfig,
    ScreeningMode,
    TicketIntake,
)

from .test_api import HEADERS, ev


def _next_monday(min_days: int = 7) -> datetime:
    d = (datetime.now(UTC) + timedelta(days=min_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return d + timedelta(days=(7 - d.weekday()) % 7)


DAY = _next_monday()
DAY_S = DAY.strftime("%Y-%m-%d")

OWNER = "+447700900555"
CALLER = "+447700900123"


def cfg(**over: Any) -> AssistantConfig:
    return AssistantConfig(
        tenant_id="demo",
        company_id="demo",
        assistant_id="demo",
        name="Ava",
        business_name="Acme Plumbing",
        **over,
    )


def call(**over: Any) -> CallRecord:
    base: dict[str, Any] = {
        "call_id": "c1",
        "tenant_id": "demo",
        "company_id": "demo",
        "assistant_id": "demo",
        "caller": CALLER,
        "status": "completed",
        "started_at": datetime(2026, 9, 11, 9, 30, tzinfo=UTC),
        "answered_at": datetime(2026, 9, 11, 9, 30, 2, tzinfo=UTC),
        "duration_s": 95,
    }
    base.update(over)
    return CallRecord.model_validate(base)


# -- owner SMS summary ------------------------------------------------------------------------


def test_owner_sms_summary_content() -> None:
    c = call(
        summary="Burst pipe under the kitchen sink, wants someone today.",
        extracted={
            "name": "Keith Wilson",
            "postcode": "M20 2AB",
            "callback_number": "+447000000001",
        },
        ticket_ids=["t1"],
        escalated=True,
    )
    s = owner_sms_summary(c, "Acme Plumbing")
    assert s.startswith("Acme Plumbing: Call 09:30 from Keith Wilson (+447700900123).")
    assert "Burst pipe" in s and "URGENT" in s
    assert "Call back: +447000000001" in s and "M20 2AB" in s
    # callback == caller number is not repeated
    c2 = call(extracted={"phone": CALLER})
    assert "Call back" not in owner_sms_summary(c2, "Acme")
    # missed call / withheld number / long summary truncation
    c3 = call(caller=None, answered_at=None, summary="x" * 400)
    s3 = owner_sms_summary(c3, "Acme")
    assert "Missed call 09:30 from Withheld" in s3 and "..." in s3 and len(s3) < 260
    assert "Caller hung up before speaking" in owner_sms_summary(call(answered_at=None), "Acme")


async def test_owner_sms_rule_delivers_and_respects_gating(
    client: AsyncClient, app: FastAPI
) -> None:
    sms: MessageService = app.state.sms
    sms.from_number = "+442046206823"
    prov = sms.provider
    assert isinstance(prov, LogSmsProvider)
    r = await client.post(
        "/v1/notifications/rules",
        params={"tenant_id": "demo"},
        json={
            "name": "Owner SMS",
            "channel": "sms",
            "target": OWNER,
            "events": ["call.completed"],
        },
    )
    assert r.status_code == 201, r.text
    rule = r.json()

    async def run_call(cid: str) -> None:
        for e in (
            ev(CallEventType.CALL_STARTED, cid, {"caller": CALLER, "dialed": "+440000000000"}),
            ev(CallEventType.CALL_ANSWERED, cid, {}),
            ev(CallEventType.CALL_ENDED, cid, {"reason": "caller_hangup", "duration_s": 40}),
        ):
            rr = await client.post("/v1/worker/events", json=e, headers=HEADERS)
            assert rr.status_code in (200, 202), rr.text
        await app.state.postcall.drain()

    before = len(prov.sent)
    await run_call("own-1")
    owner_msgs = [m for m in prov.sent[before:] if m[1] == OWNER]
    assert len(owner_msgs) == 1
    assert owner_msgs[0][2].startswith("Parlio Demo") or ": Call " in owner_msgs[0][2]

    # paused rule -> nothing sent to the owner
    r = await client.put(
        f"/v1/notifications/rules/{rule['id']}",
        params={"tenant_id": "demo"},
        json={**rule, "enabled": False},
    )
    assert r.status_code == 200, r.text
    before = len(prov.sent)
    await run_call("own-2")
    assert not [m for m in prov.sent[before:] if m[1] == OWNER]


# -- SMS appointment reminders ----------------------------------------------------------------


def test_interpret_reply() -> None:
    assert interpret_reply("1") == ReminderStatus.CONFIRMED
    assert interpret_reply(" YES ") == ReminderStatus.CONFIRMED
    assert interpret_reply("Confirmed.") == ReminderStatus.CONFIRMED
    assert interpret_reply("2") == ReminderStatus.RESCHEDULE_REQUESTED
    assert interpret_reply("change") == ReminderStatus.RESCHEDULE_REQUESTED
    assert interpret_reply("can't make it") == ReminderStatus.RESCHEDULE_REQUESTED
    assert interpret_reply("do you have parking?") is None
    assert interpret_reply("") is None
    assert interpret_reply("12") is None


def _booking(start: datetime, *, phone: str | None = CALLER, bid: str = "bk-1") -> Booking:
    return Booking(
        id=bid,
        tenant_id="demo",
        company_id="demo",
        connection_id="cal-1",
        start=start,
        end=start + timedelta(minutes=30),
        name="Sam Jones",
        phone=phone,
    )


class _Reminders:
    def __init__(self) -> None:
        self.store = MemoryStore(None)
        self.prov = LogSmsProvider()
        self.sms = MessageService(self.store, self.prov, "+442046206823")
        self.tickets: list[TicketIntake] = []

        async def business(tenant_id: str) -> str:
            return "Acme Plumbing"

        async def on_ticket(tenant_id: str, company_id: str, intake: TicketIntake) -> Any:
            self.tickets.append(intake)
            return type("T", (), {"id": f"tk-{len(self.tickets)}"})()

        self.svc = ReminderService(
            self.store, self.sms, business_name=business, on_ticket=on_ticket
        )


async def test_reminders_scheduled_sent_and_confirmed() -> None:
    h = _Reminders()
    now = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    # disabled by default -> nothing scheduled
    assert await h.svc.on_booking(_booking(now + timedelta(days=2)), now) == []
    await h.svc.set_policy(ReminderPolicy(tenant_id="demo", enabled=True, hours_before=[24, 2]))
    await h.store.put_doc(_booking(now + timedelta(days=2)).to_doc())
    rs = await h.svc.on_booking(_booking(now + timedelta(days=2)), now)
    assert [r.status for r in rs] == [ReminderStatus.SCHEDULED] * 2
    assert sorted(r.send_at for r in rs) == [
        now + timedelta(days=1),
        now + timedelta(days=2, hours=-2),
    ]
    # no phone / past appointment / send time already passed -> nothing
    assert (
        await h.svc.on_booking(_booking(now + timedelta(days=2), phone=None, bid="b2"), now) == []
    )
    assert await h.svc.on_booking(_booking(now - timedelta(hours=1), bid="b3"), now) == []
    assert await h.svc.on_booking(_booking(now + timedelta(hours=1), bid="b4"), now) == []

    # not due yet
    assert await h.svc.sweep(now) == 0
    assert await h.svc.sweep(now + timedelta(days=1, minutes=1)) == 1
    sent = h.prov.sent[-1]
    assert sent[1] == CALLER
    assert "Acme Plumbing: reminder of your appointment on" in sent[2]
    assert "Reply 1 to confirm or 2 to reschedule" in sent[2]
    # tenant timezone: 09:00 UTC on 13 Sep = 10:00 BST
    assert "10:00" in sent[2]

    # invalid reply -> None (falls through to Inbox AI)
    assert (
        await h.svc.handle_reply("demo", CALLER, "do you do boilers?", now + timedelta(days=1))
        is None
    )
    # unknown number -> None
    assert await h.svc.handle_reply("demo", "+447700900999", "1", now + timedelta(days=1)) is None

    rep = await h.svc.handle_reply("demo", CALLER, "YES", now + timedelta(days=1, minutes=5))
    assert rep is not None and "confirmed" in rep.text.lower() and "Sam" in rep.text
    rs = await h.svc.list_for("demo")
    by_status = {r.status for r in rs}
    assert ReminderStatus.CONFIRMED in by_status
    bk = await h.store.get_doc("booking", "bk-1")
    assert bk is not None and bk.data["status"] == "confirmed"
    assert h.tickets == []
    # the 2h reminder still goes out (confirmed appointments still get the final nudge)
    assert await h.svc.sweep(now + timedelta(days=2, hours=-1)) == 1


async def test_reminder_reschedule_creates_ticket_and_cancels_rest() -> None:
    h = _Reminders()
    now = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    await h.svc.set_policy(ReminderPolicy(tenant_id="demo", enabled=True, hours_before=[24, 2]))
    b = _booking(now + timedelta(days=2))
    await h.store.put_doc(b.to_doc())
    await h.svc.on_booking(b, now)
    assert await h.svc.sweep(now + timedelta(days=1)) == 1

    rep = await h.svc.handle_reply("demo", CALLER, "2", now + timedelta(days=1, minutes=10))
    assert rep is not None and "call you" in rep.text.lower()
    assert len(h.tickets) == 1
    assert h.tickets[0].caller_number == CALLER
    assert "reschedule" in h.tickets[0].reason.lower()
    bk = await h.store.get_doc("booking", "bk-1")
    assert bk is not None and bk.data["status"] == "reschedule_requested"
    statuses = sorted(r.status for r in await h.svc.list_for("demo"))
    assert statuses == [ReminderStatus.CANCELLED, ReminderStatus.RESCHEDULE_REQUESTED]
    # duplicate reply after resolution -> ignored, no second ticket
    assert (
        await h.svc.handle_reply("demo", CALLER, "2", now + timedelta(days=1, minutes=20)) is None
    )
    assert len(h.tickets) == 1
    # nothing else goes out
    assert await h.svc.sweep(now + timedelta(days=2)) == 0


async def test_reminder_skipped_when_booking_cancelled_or_reply_too_late() -> None:
    h = _Reminders()
    now = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)
    await h.svc.set_policy(ReminderPolicy(tenant_id="demo", enabled=True, hours_before=[24]))
    b = _booking(now + timedelta(days=2))
    await h.store.put_doc(b.to_doc())
    await h.svc.on_booking(b, now)
    b.status = "cancelled"
    await h.store.put_doc(b.to_doc())
    assert await h.svc.sweep(now + timedelta(days=1)) == 0
    assert h.prov.sent == []
    rs = await h.svc.list_for("demo")
    assert rs[0].status == ReminderStatus.CANCELLED

    # a second booking: reminder sent, but the reply arrives after the appointment
    b2 = _booking(now + timedelta(days=2), bid="bk-2")
    await h.store.put_doc(b2.to_doc())
    await h.svc.on_booking(b2, now)
    assert await h.svc.sweep(now + timedelta(days=1)) == 1
    assert await h.svc.handle_reply("demo", CALLER, "1", now + timedelta(days=3)) is None


async def test_reminder_policy_api_and_inbox_reply_routing(
    client: AsyncClient, app: FastAPI
) -> None:
    r = await client.get("/v1/reminders/policy", params={"tenant_id": "demo"})
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await client.put(
        "/v1/reminders/policy",
        params={"tenant_id": "demo"},
        json={"enabled": True, "hours_before": []},
    )
    assert r.status_code == 422
    r = await client.put(
        "/v1/reminders/policy",
        params={"tenant_id": "demo"},
        json={"enabled": True, "hours_before": [48, 2]},
    )
    assert r.status_code == 200 and r.json()["hours_before"] == [48, 2]
    # other tenant is isolated
    r = await client.get("/v1/reminders/policy", params={"tenant_id": "other"})
    assert r.status_code in (403, 200)

    # book via the simulated diary -> reminder scheduled
    r = await client.post(
        "/v1/calendar/connections",
        params={"tenant_id": "demo"},
        json={"provider": "simulated", "name": "Diary"},
    )
    assert r.status_code == 201
    start = DAY.replace(hour=10)
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params={"tenant_id": "demo"},
        json={"start": start.isoformat(), "name": "Sam", "phone": CALLER},
        headers=HEADERS,
    )
    assert r.status_code == 201, r.text
    r = await client.get("/v1/reminders", params={"tenant_id": "demo"})
    assert r.status_code == 200
    assert [x["status"] for x in r.json()] == ["scheduled", "scheduled"]

    # force-send, then reply via the carrier webhook -> reminder confirmed, not the Inbox AI
    sms: MessageService = app.state.sms
    sms.from_number = "+442046206823"
    svc: ReminderService = app.state.reminders
    assert await svc.sweep(start - timedelta(hours=1)) == 2
    from .test_inbox import telnyx_sms

    r = await client.post("/v1/inbound/sms", json=telnyx_sms("1", frm=CALLER))
    assert r.status_code == 202, r.text
    r = await client.get("/v1/reminders", params={"tenant_id": "demo"})
    assert "confirmed" in [x["status"] for x in r.json()]
    prov = sms.provider
    assert isinstance(prov, LogSmsProvider)
    assert any("confirmed" in m[2].lower() and m[1] == CALLER for m in prov.sent)


# -- screening & spam -------------------------------------------------------------------------


async def _assess(
    svc: ScreeningService, sc: ScreeningConfig, caller: str | None, **over: Any
) -> tuple[ScreeningAction, str]:
    v = await svc.assess(cfg(screening=sc, **over), caller)
    return v.action, v.reason


async def test_screening_verdicts() -> None:
    store = MemoryStore(None)
    svc = ScreeningService(store)
    off = ScreeningConfig()
    unknown = ScreeningConfig(mode=ScreeningMode.UNKNOWN)
    every = ScreeningConfig(mode=ScreeningMode.ALL)

    # default config: legitimate unknown caller is allowed straight through
    assert (await _assess(svc, off, CALLER))[0] == ScreeningAction.ALLOW
    # blocked numbers on the assistant win over everything
    assert await _assess(svc, off, CALLER, blocked_numbers=[CALLER]) == (
        ScreeningAction.REJECT,
        "blocked number",
    )
    # withheld
    assert (await _assess(svc, off, None))[0] == ScreeningAction.ALLOW
    assert (await _assess(svc, unknown, "anonymous"))[0] == ScreeningAction.SCREEN
    assert (await _assess(svc, ScreeningConfig(block_withheld=True), None))[
        0
    ] == ScreeningAction.REJECT
    # unknown mode screens strangers, allow list bypasses
    assert await _assess(svc, unknown, CALLER) == (ScreeningAction.SCREEN, "unknown caller")
    allowed = ScreeningConfig(mode=ScreeningMode.UNKNOWN, allow_numbers=["07700 900123"])
    assert await _assess(svc, allowed, CALLER) == (ScreeningAction.ALLOW, "allow list")

    # known contact (named) is not screened in unknown mode
    cid, _ = await store.touch_contact("demo", "demo", CALLER)
    await store.update_contact(cid, ContactUpdate(name="Sam Jones", status="customer"))
    v = await svc.assess(cfg(screening=unknown), CALLER)
    assert v.action == ScreeningAction.ALLOW and v.known_name == "Sam Jones"
    assert v.contact_status == "customer"
    # ...but is in "all" mode, with identity passed along
    v = await svc.assess(cfg(screening=every), CALLER)
    assert v.action == ScreeningAction.SCREEN and v.known_name == "Sam Jones"
    # blocked contact is rejected even when screening is off
    await store.update_contact(cid, ContactUpdate(status="blocked"))
    assert await _assess(svc, off, CALLER) == (ScreeningAction.REJECT, "contact blocked")

    # tenant + platform spam lists (block_spam on by default; off disables)
    other = "+447700900777"
    await svc.report_spam("demo", other, reason="sales")
    assert await _assess(svc, off, other) == (ScreeningAction.REJECT, "tenant spam list")
    assert await _assess(svc, ScreeningConfig(block_spam=False), other) == (
        ScreeningAction.ALLOW,
        "screening off",
    )
    assert await svc.forgive("demo", other) is True
    assert (await _assess(svc, off, other))[0] == ScreeningAction.ALLOW
    await svc.report_spam(PLATFORM_TENANT, other, reason="known robocaller")
    assert await _assess(svc, off, other) == (ScreeningAction.REJECT, "platform spam list")
    assert [s.e164 for s in await svc.spam_list("demo")] == []


def test_robocaller_heuristic() -> None:
    now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    spam = "+441000000001"

    def short(i: int, **over: Any) -> CallRecord:
        return call(
            **{
                "call_id": f"s{i}",
                "caller": spam,
                "started_at": now - timedelta(hours=i),
                "duration_s": 3,
                **over,
            }
        )

    assert looks_like_robocaller(spam, [short(1), short(2)], now) is False
    assert looks_like_robocaller(spam, [short(1), short(2), short(3)], now) is True
    # a real conversation, ticket or transfer in the mix clears it
    assert looks_like_robocaller(spam, [short(1), short(2), short(3, duration_s=60)], now) is False
    assert (
        looks_like_robocaller(spam, [short(1), short(2), short(3, ticket_ids=["t"])], now) is False
    )
    # old calls and outbound calls do not count
    assert looks_like_robocaller(spam, [short(1), short(2), short(30)], now) is False
    assert (
        looks_like_robocaller(spam, [short(1), short(2), short(3, direction="outbound")], now)
        is False
    )


async def test_screening_worker_route_spam_api_and_calls_list(client: AsyncClient) -> None:
    r = await client.get(
        "/v1/worker/screening", params={"assistant_id": "demo", "caller": CALLER}, headers=HEADERS
    )
    assert r.status_code == 200 and r.json()["action"] == "allow"
    r = await client.post(
        "/v1/screening/spam", params={"tenant_id": "demo"}, json={"e164": CALLER, "reason": "sales"}
    )
    assert r.status_code == 201, r.text
    r = await client.get("/v1/screening/spam", params={"tenant_id": "demo"})
    assert [s["e164"] for s in r.json()] == [CALLER]
    r = await client.get(
        "/v1/worker/screening", params={"assistant_id": "demo", "caller": CALLER}, headers=HEADERS
    )
    assert r.json() == {
        "action": "reject",
        "reason": "tenant spam list",
        "known_name": None,
        "contact_status": None,
    }
    r = await client.post("/v1/screening/spam", params={"tenant_id": "demo"}, json={"e164": "abc"})
    assert r.status_code == 422
    # a screened-out call shows as blocked in the Calls list
    for e in (
        ev(CallEventType.CALL_STARTED, "scr-1", {"caller": CALLER, "dialed": "+440000000000"}),
        ev(CallEventType.CALL_ENDED, "scr-1", {"reason": "screened", "duration_s": 0}),
    ):
        rr = await client.post("/v1/worker/events", json=e, headers=HEADERS)
        assert rr.status_code in (200, 202), rr.text
    r = await client.get("/v1/calls/scr-1", params={"tenant_id": "demo"})
    assert r.status_code == 200 and r.json()["kind"] == "blocked"
    assert r.json()["end_reason"] == "screened"
    r = await client.delete("/v1/screening/spam", params={"tenant_id": "demo", "e164": CALLER})
    assert r.status_code == 204
    r = await client.delete("/v1/screening/spam", params={"tenant_id": "demo", "e164": CALLER})
    assert r.status_code == 404


# -- Google + Microsoft calendar (mocked HTTP) ------------------------------------------------


class _Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        body: Any = None
        if req.content:
            try:
                body = json.loads(req.content)
            except ValueError:
                body = req.content.decode()
        self.calls.append((req.method, url.split("?")[0], body))
        # OAuth token endpoints (Google + Microsoft)
        if url.startswith(GoogleCalendarBackend.TOKEN) or url.startswith(
            MicrosoftCalendarBackend.TOKEN
        ):
            grant = "refresh_token" if "grant_type=refresh_token" in str(body) else "code"
            return httpx.Response(
                200,
                json={"access_token": f"at-{grant}", "refresh_token": "rt-1", "expires_in": 3600},
            )
        if url.startswith("https://openidconnect.googleapis.com/v1/userinfo"):
            return httpx.Response(200, json={"email": "owner@acme.example"})
        if url.startswith(f"{MicrosoftCalendarBackend.GRAPH}/me/calendarView"):
            assert req.headers["Authorization"] == "Bearer at-refresh_token"
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "showAs": "busy",
                            "start": {"dateTime": f"{DAY_S}T09:00:00.0000000"},
                            "end": {"dateTime": f"{DAY_S}T10:00:00.0000000"},
                        },
                        {
                            "showAs": "free",
                            "start": {"dateTime": f"{DAY_S}T11:00:00.0000000"},
                            "end": {"dateTime": f"{DAY_S}T12:00:00.0000000"},
                        },
                    ]
                },
            )
        if url.startswith(f"{MicrosoftCalendarBackend.GRAPH}/me/events"):
            return httpx.Response(201, json={"id": "AAMk-evt-1"})
        if url.startswith(f"{MicrosoftCalendarBackend.GRAPH}/me"):
            return httpx.Response(200, json={"userPrincipalName": "owner@acme.onmicrosoft.com"})
        if url.startswith(f"{GoogleCalendarBackend.API}/freeBusy"):
            assert req.headers["Authorization"] == "Bearer at-refresh_token"
            return httpx.Response(
                200,
                json={
                    "calendars": {
                        "primary": {
                            "busy": [
                                {
                                    "start": f"{DAY_S}T09:00:00+00:00",
                                    "end": f"{DAY_S}T10:00:00+00:00",
                                }
                            ]
                        }
                    }
                },
            )
        if url.startswith(f"{GoogleCalendarBackend.API}/calendars/"):
            return httpx.Response(200, json={"id": "gevt-1"})
        return httpx.Response(404, json={"error": url})


def _service(vault: LocalVault, fake: _Fake) -> CalendarService:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    store = MemoryStore(None)
    return CalendarService(
        store,
        vault,
        backends={
            CalendarProvider.GOOGLE: GoogleCalendarBackend("gid", "gsecret", vault, http),
            CalendarProvider.MICROSOFT: MicrosoftCalendarBackend("mid", "msecret", vault, http),
        },
        dashboard_url="https://app.parlio.test/",
    )


@pytest.mark.parametrize("provider", [CalendarProvider.MICROSOFT, CalendarProvider.GOOGLE])
async def test_oauth_freebusy_and_booking_mocked(provider: CalendarProvider) -> None:
    vault = LocalVault("test-key-for-calendar-tests")
    fake = _Fake()
    svc = _service(vault, fake)
    redirect = "https://api.example/v1/public/calendar/oauth/callback"

    url = await svc.oauth_start("demo", "demo", provider, redirect)
    if provider == CalendarProvider.MICROSOFT:
        assert url.startswith(MicrosoftCalendarBackend.AUTH) and "Calendars.ReadWrite" in url
        assert "client_id=mid" in url
    else:
        assert url.startswith(GoogleCalendarBackend.AUTH) and "access_type=offline" in url
    state = url.split("state=")[1].split("&")[0]

    conn = await svc.oauth_callback(state, "auth-code-1")
    assert conn.provider == provider and conn.status == ConnectionStatus.CONNECTED
    assert conn.account_email and "@acme" in conn.account_email
    assert conn.token_sealed and vault.open(conn.token_sealed) == "rt-1"
    assert "token_sealed" not in conn.public()
    tok_call = next(c for c in fake.calls if c[0] == "POST" and "token" in c[1])
    assert "code=auth-code-1" in tok_call[2] and "client_secret" in tok_call[2]
    # bad state is rejected
    with pytest.raises(ValueError):
        await svc.oauth_callback("nope", "x")

    # free/busy: 09:00-10:00 busy -> availability excludes it, "free" events ignored
    day = DAY
    be = svc.backends[provider]
    busy = await be.busy(conn, day, day + timedelta(days=1))
    assert busy == [Slot(start=day.replace(hour=9), end=day.replace(hour=10))]

    booked = await svc.book(
        "demo",
        BookingRequest(
            start=day.replace(hour=10),
            name="Sam",
            phone=CALLER,
            notes="Boiler losing pressure, no hot water upstairs",
            address="12 High Street, Leeds LS1 4AB",
            call_id="call-1",
            connection_id=conn.id,
        ),
    )
    assert booked.provider_ref in ("AAMk-evt-1", "gevt-1")
    create = fake.calls[-1]
    assert create[0] == "POST" and "events" in create[1]
    ms = provider == CalendarProvider.MICROSOFT
    assert create[2]["subject" if ms else "summary"].startswith("Sam")
    body = create[2]["body"]["content"] if ms else create[2]["description"]
    for needle in (CALLER, "no hot water", "LS1 4AB", "Details:", "/calls/call-1"):
        assert needle in body, body
    loc = create[2]["location"]["displayName"] if ms else create[2]["location"]
    assert loc == "12 High Street, Leeds LS1 4AB"
    # overlapping slot is refused before any event is created
    n = len(fake.calls)
    with pytest.raises(ValueError, match="no longer available"):
        await svc.book(
            "demo",
            BookingRequest(start=day.replace(hour=9, minute=30), name="X", connection_id=conn.id),
        )
    assert not any("events" in c[1] for c in fake.calls[n:])
    log = await svc.sync_log("demo")
    assert [e.ok for e in log[:3]] == [False, True, True] or {e.ok for e in log} == {True, False}
    assert (await svc.bookings("demo"))[0].id == booked.id


async def test_calendar_provider_status_api(client: AsyncClient, app: FastAPI) -> None:
    # neither Google nor Microsoft client creds in tests -> OAuth start says "not configured"
    for p in ("google", "microsoft"):
        r = await client.get(
            "/v1/calendar/oauth/start", params={"tenant_id": "demo", "provider": p}
        )
        assert r.status_code in (400, 409, 422), (p, r.text)
        assert "not configured" in r.text
    # connections list is per tenant and never leaks sealed tokens
    r = await client.post(
        "/v1/calendar/connections",
        params={"tenant_id": "demo"},
        json={"provider": "simulated", "name": "Diary"},
    )
    assert r.status_code == 201
    r = await client.get("/v1/calendar/connections", params={"tenant_id": "demo"})
    assert r.status_code == 200 and len(r.json()) == 1
    assert all("token_sealed" not in c for c in r.json())
    assert (
        CalendarConnection.model_validate(
            {**r.json()[0], "tenant_id": "demo", "company_id": "demo"}
        ).provider
        == CalendarProvider.SIMULATED
    )


# -- SMS opt-out (STOP / START) ----------------------------------------------------------------


async def test_sms_stop_opts_out_and_start_opts_back_in(client: AsyncClient, app: FastAPI) -> None:
    from .test_inbox import telnyx_sms

    sms: MessageService = app.state.sms
    sms.from_number = "+442046206823"
    prov = sms.provider
    assert isinstance(prov, LogSmsProvider)

    r = await client.post("/v1/inbound/sms", json=telnyx_sms("STOP", frm=CALLER))
    assert r.status_code == 202, r.text
    assert await sms.opted_out("demo", CALLER)
    assert prov.sent[-1][1] == CALLER and "unsubscribed" in prov.sent[-1][2]

    # ordinary sends are now suppressed and logged as skipped
    m = await sms.send("demo", "demo", CALLER, "hello")
    assert m.status == MessageStatus.SKIPPED and "opted out" in (m.error or "")
    before = len(prov.sent)
    # ...including reminder / AI replies routed through the inbox
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("do you have parking?", frm=CALLER))
    assert r.status_code == 202
    assert not [x for x in prov.sent[before:] if x[1] == CALLER]

    r = await client.post("/v1/inbound/sms", json=telnyx_sms("start", frm=CALLER))
    assert r.status_code == 202
    assert not await sms.opted_out("demo", CALLER)
    assert "opted back in" in prov.sent[-1][2]
    assert (await sms.send("demo", "demo", CALLER, "hello")).status == MessageStatus.SENT
