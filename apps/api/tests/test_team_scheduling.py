"""Phase 22 — engineers as resources, pooled availability + assignment, SchedulingBackend, and the
Schedule read model. Everything runs against the simulated calendar / scheduler; the webhook and
ServiceM8 adapters are exercised against `httpx.MockTransport`."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from parlio_api.calendar import Booking
from parlio_api.resources import (
    AssignmentPolicy,
    Resource,
    area_matches,
    choose,
    eligible,
    outward_code,
)
from parlio_api.scheduling import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    ExternalJob,
    SchedulerConfig,
    SchedulerProvider,
    ServiceM8Scheduler,
    WebhookScheduler,
    sign,
    verify,
)
from parlio_voice.models import DayHours, Schedule

from .test_api import HEADERS

T = {"tenant_id": "demo"}


def _res(name: str, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "role": "Engineer",
        "skills": [],
        "site_id": None,
        "areas": [],
        "hours": None,
        "active": True,
        "on_call": False,
        "connection_id": None,
        "calendar_id": "primary",
        "external_ref": None,
        "phone": None,
    }
    body.update(kw)
    return body


async def _diary(client: AsyncClient, services: list[dict[str, Any]] | None = None) -> str:
    r = await client.post(
        "/v1/calendar/connections", params=T, json={"provider": "simulated", "name": "Diary"}
    )
    assert r.status_code == 201, r.text
    conn_id = str(r.json()["id"])
    if services:
        rules = {
            "slot_minutes": 60,
            "buffer_minutes": 30,
            "rules": {"align_minutes": 30, "use_business_hours": False, "services": services},
        }
        r = await client.put(f"/v1/calendar/connections/{conn_id}/rules", params=T, json=rules)
        assert r.status_code == 200, r.text
    return conn_id


# -- 22a: model helpers ---------------------------------------------------------------------------


def test_outward_code_and_area_matching() -> None:
    assert outward_code("12 High St, Manchester M1 4BT") == "M1"
    assert outward_code("sk8 3ab") == "SK8"
    assert outward_code("SW1A 1AA") == "SW1A"
    assert outward_code("M1") == "M1"
    assert outward_code("nowhere") is None
    assert outward_code(None) is None
    assert area_matches("M", "M14")
    assert area_matches("m1", "M1")
    assert area_matches("M1", "M14")  # prefix: M1 covers M14
    assert not area_matches("SK", "M1")
    assert not area_matches("", "M1")


def _names(rs: list[Resource]) -> list[str]:
    return [r.name for r in rs]


def test_eligible_filters_by_skill_site_and_on_call() -> None:
    a = Resource(tenant_id="t", name="A", skills=["Boiler service"])
    b = Resource(tenant_id="t", name="B", skills=["Repair"], on_call=True)
    c = Resource(tenant_id="t", name="C", active=False)
    d = Resource(tenant_id="t", name="D", site_id="leeds")
    team = [a, b, c, d]
    assert _names(eligible(team)) == ["A", "B", "D"]
    assert _names(eligible(team, service_name="Boiler service")) == ["A", "D"]
    assert _names(eligible(team, service_name="Repair", emergency=True)) == ["B"]
    assert _names(eligible(team, service_name="Repair", emergency=True, prefer_on_call=False)) == [
        "B",
        "D",
    ]
    assert _names(eligible(team, site_id="leeds")) == ["A", "B", "D"]
    assert _names(eligible(team, site_id="york")) == ["A", "B"]


def test_choose_policies() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    a = Resource(tenant_id="t", name="A", areas=["M"], created_at=now)
    b = Resource(tenant_id="t", name="B", areas=["M1", "M2"], created_at=now + timedelta(1))
    c = Resource(tenant_id="t", name="C", areas=["SK"], created_at=now + timedelta(2))
    pool = [a, b, c]
    load = Counter({"": 0, a.id: 2, b.id: 1, c.id: 1})
    least = choose(pool, AssignmentPolicy.LEAST_LOADED, day_load=load)
    assert least is b  # tie with c broken by creation order
    assert choose(pool, AssignmentPolicy.ROUND_ROBIN, day_load=load, cursor=0) is a
    assert choose(pool, AssignmentPolicy.ROUND_ROBIN, day_load=load, cursor=4) is b
    nearest = choose(pool, AssignmentPolicy.NEAREST, day_load=load, postcode_area="M1")
    assert nearest is b  # longest prefix match wins over least-loaded
    far = choose(pool, AssignmentPolicy.NEAREST, day_load=load, postcode_area="LS1")
    assert far is b  # nobody covers LS → falls back to least loaded
    pref = choose(pool, AssignmentPolicy.PREFERRED, day_load=load, preferred_id=c.id)
    assert pref is c
    # preferred engineer is ignored unless the tenant picked that policy
    assert choose(pool, AssignmentPolicy.LEAST_LOADED, day_load=load, preferred_id=c.id) is b
    assert choose([], AssignmentPolicy.LEAST_LOADED, day_load=load) is None


# -- 22a: CRUD + tenant isolation ------------------------------------------------------------------


async def test_resource_crud_and_tenant_isolation(client: AsyncClient) -> None:
    r = await client.post("/v1/team/resources", params=T, json=_res("Ann", skills=["Repair"]))
    assert r.status_code == 201, r.text
    ann = r.json()
    assert ann["id"].startswith("res-") and ann["tenant_id"] == "demo"
    r = await client.get("/v1/team/resources", params=T)
    assert [x["name"] for x in r.json()] == ["Ann"]
    r = await client.put(
        f"/v1/team/resources/{ann['id']}", params=T, json=_res("Ann B", active=False)
    )
    assert r.status_code == 200 and r.json()["active"] is False
    # other tenants cannot see or touch it
    r = await client.get("/v1/team/resources", params={"tenant_id": "other"})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert r.json() == []
    r = await client.put(
        f"/v1/team/resources/{ann['id']}", params={"tenant_id": "other"}, json=_res("Hack")
    )
    assert r.status_code in (403, 404)
    r = await client.delete(f"/v1/team/resources/{ann['id']}", params=T)
    assert r.status_code == 204
    r = await client.get("/v1/team/resources", params=T)
    assert r.json() == []
    # team settings round-trip
    r = await client.put(
        "/v1/team/settings",
        params=T,
        json={"policy": "round_robin", "mode": "calendar", "emergency_to_on_call": False},
    )
    assert r.status_code == 200 and r.json()["policy"] == "round_robin"
    r = await client.get("/v1/team/settings", params=T)
    assert r.json()["emergency_to_on_call"] is False


# -- 22b: pooled availability + assignment ---------------------------------------------------------


def _weekday_hours(open_: int, close: int) -> dict[str, Any]:
    s = Schedule(
        timezone="UTC",
        hours={
            d: DayHours(open=dtime(open_), close=dtime(close))
            for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        },
    )
    return s.model_dump(mode="json")


async def test_pooled_availability_assigns_and_moves_events(client: AsyncClient) -> None:
    await _diary(
        client,
        services=[
            {"id": "svc-rep", "name": "Repair", "minutes": 60},
            {"id": "svc-gas", "name": "Gas emergency", "minutes": 60, "emergency": True},
        ],
    )
    r = await client.post(
        "/v1/team/resources",
        params=T,
        json=_res(
            "Ann", skills=["Repair"], areas=["M"], hours=_weekday_hours(8, 17), calendar_id="ann"
        ),
    )
    ann = r.json()
    r = await client.post(
        "/v1/team/resources",
        params=T,
        json=_res(
            "Bob",
            skills=["Repair", "Gas emergency"],
            areas=["SK"],
            on_call=True,
            hours=_weekday_hours(8, 17),
            calendar_id="bob",
        ),
    )
    bob = r.json()
    await client.post(
        "/v1/team/resources", params=T, json=_res("Cara", skills=["Tiling"], calendar_id="cara")
    )

    # caller never picks a person: slots come back pooled across Ann + Bob
    r = await client.get(
        "/v1/worker/calendar/availability",
        params={**T, "days": 7, "service_id": "svc-rep"},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text
    avail = r.json()
    assert avail["slots"] and avail["resources"] == 2
    # nobody does "Tiling" as a service type; unknown service is rejected
    r = await client.get(
        "/v1/worker/calendar/availability",
        params={**T, "days": 7, "service_id": "nope"},
        headers=HEADERS,
    )
    assert r.json()["error"]

    # nearest policy: an M1 address goes to Ann
    await client.put(
        "/v1/team/settings",
        params=T,
        json={"policy": "nearest", "mode": "calendar", "emergency_to_on_call": True},
    )
    slot = avail["slots"][0]["start"]
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params=T,
        headers=HEADERS,
        json={
            "start": slot,
            "name": "Sam",
            "phone": "+447700900123",
            "service_id": "svc-rep",
            "address": "1 Piccadilly, Manchester M1 1AA",
        },
    )
    assert r.status_code == 201, r.text
    b1 = Booking.model_validate(r.json())
    assert b1.resource_id == ann["id"] and b1.resource_name == "Ann"
    assert b1.postcode_area == "M1" and b1.provider_ref

    # same slot again: Ann is busy, so Bob (still free) gets it even though he's not nearest
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params=T,
        headers=HEADERS,
        json={"start": slot, "name": "Jo", "service_id": "svc-rep", "address": "M1 2AB"},
    )
    assert r.status_code == 201, r.text
    b2 = Booking.model_validate(r.json())
    assert b2.resource_id == bob["id"]

    # third caller for the same slot: nobody eligible is free
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params=T,
        headers=HEADERS,
        json={"start": slot, "name": "Late", "service_id": "svc-rep"},
    )
    assert r.status_code in (400, 409), r.text

    # emergencies go to the on-call engineer
    r = await client.get(
        "/v1/worker/calendar/availability",
        params={**T, "days": 7, "service_id": "svc-gas"},
        headers=HEADERS,
    )
    gas = r.json()
    assert gas["resources"] == 1
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params=T,
        headers=HEADERS,
        json={"start": gas["slots"][0]["start"], "name": "Em", "service_id": "svc-gas"},
    )
    assert r.status_code == 201 and r.json()["resource_id"] == bob["id"]

    # reassign: Bob → Ann clashes (Ann holds b1 at that time); Jo's booking moved to Cara is ok
    r = await client.post(
        f"/v1/team/bookings/{b2.id}/reassign", params=T, json={"resource_id": ann["id"]}
    )
    assert r.status_code == 409, r.text
    cara = next(
        x for x in (await client.get("/v1/team/resources", params=T)).json() if x["name"] == "Cara"
    )
    r = await client.post(
        f"/v1/team/bookings/{b2.id}/reassign", params=T, json={"resource_id": cara["id"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["resource_id"] == cara["id"] and r.json()["resource_name"] == "Cara"

    # reschedule b1 to a later free slot, then cancel
    later = next(s["start"] for s in avail["slots"] if s["start"] != slot)
    r = await client.post(f"/v1/team/bookings/{b1.id}/reschedule", params=T, json={"start": later})
    assert r.status_code == 200, r.text
    assert r.json()["start"].startswith(later[:16]) and r.json()["resource_id"] == ann["id"]
    r = await client.post(f"/v1/team/bookings/{b1.id}/cancel", params=T)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    r = await client.get(f"/v1/team/bookings/{b1.id}", params={"tenant_id": "other"})
    assert r.status_code in (403, 404)
    r = await client.post(f"/v1/team/bookings/{b1.id}/cancel", params={"tenant_id": "other"})
    assert r.status_code in (403, 404)

    # bookings surface the assignee
    r = await client.get("/v1/calendar/bookings", params=T)
    names = {b["id"]: b["resource_name"] for b in r.json()}
    assert names[b2.id] == "Cara"


async def test_round_robin_rotates(client: AsyncClient) -> None:
    await _diary(client)
    for n in ("A", "B", "C"):
        await client.post("/v1/team/resources", params=T, json=_res(n, calendar_id=n))
    await client.put(
        "/v1/team/settings",
        params=T,
        json={"policy": "round_robin", "mode": "calendar", "emergency_to_on_call": True},
    )
    r = await client.get(
        "/v1/worker/calendar/availability", params={**T, "days": 7}, headers=HEADERS
    )
    slots = [s["start"] for s in r.json()["slots"]]
    got = []
    for i in range(3):
        r = await client.post(
            "/v1/worker/calendar/bookings",
            params=T,
            headers=HEADERS,
            json={"start": slots[i], "name": f"Caller {i}"},
        )
        assert r.status_code == 201, r.text
        got.append(r.json()["resource_name"])
    assert got == ["A", "B", "C"]


async def test_no_resources_keeps_single_calendar_behaviour(client: AsyncClient) -> None:
    await _diary(client)
    r = await client.get(
        "/v1/worker/calendar/availability", params={**T, "days": 7}, headers=HEADERS
    )
    assert r.json()["slots"] and r.json()["resources"] == 0
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params=T,
        headers=HEADERS,
        json={"start": r.json()["slots"][0]["start"], "name": "Solo"},
    )
    assert r.status_code == 201 and r.json()["resource_id"] is None


# -- 22c: SchedulingBackend -------------------------------------------------------------


def test_hmac_sign_and_verify() -> None:
    ts = str(int(time.time()))
    body = b'{"event":"job.cancelled"}'
    sig = sign("s3cret", ts, body)
    assert sig.startswith("sha256=")
    assert verify("s3cret", ts, sig, body)
    assert not verify("other", ts, sig, body)
    assert not verify("s3cret", ts, sig, body + b" ")
    assert not verify("s3cret", None, sig, body)
    assert not verify("s3cret", ts, None, body)
    assert not verify("s3cret", str(int(time.time()) - 600), sign("s3cret", "0", body), body)
    assert not verify("s3cret", "not-a-number", sig, body)


async def test_webhook_scheduler_signs_requests() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        path = req.url.path
        if path == "/resources":
            return httpx.Response(200, json={"resources": [{"id": "e1", "name": "Ext One"}]})
        if path == "/availability":
            return httpx.Response(
                200,
                json={
                    "slots": [
                        {
                            "start": "2026-10-01T09:00:00Z",
                            "end": "2026-10-01T10:00:00Z",
                            "resource_id": "e1",
                        }
                    ]
                },
            )
        if path == "/jobs" and req.method == "POST":
            return httpx.Response(201, json={"ref": "job-9"})
        if path.startswith("/jobs/") and req.method in ("PATCH", "DELETE"):
            return httpx.Response(204)
        if path == "/jobs":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {"start": "2026-10-01T11:00:00+00:00", "end": "2026-10-01T12:00:00+00:00"}
                    ]
                },
            )
        return httpx.Response(404)

    be = WebhookScheduler(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    cfg = SchedulerConfig(
        tenant_id="demo", provider=SchedulerProvider.WEBHOOK, base_url="https://sched.example/"
    )
    staff = await be.resources(cfg, "k")
    assert [s.name for s in staff] == ["Ext One"]
    start = datetime(2026, 10, 1, tzinfo=UTC)
    slots = await be.availability(
        cfg, "k", start=start, end=start + timedelta(1), minutes=60, service="Repair", area="M1"
    )
    assert slots[0].resource_id == "e1"
    job = ExternalJob(
        start=slots[0].start,
        end=slots[0].end,
        customer="Sam",
        phone="+447700900123",
        minutes=60,
        booking_id="bk-1",
    )
    assert await be.create_job(cfg, "k", job) == "job-9"
    await be.update_job(cfg, "k", "job-9", job)
    await be.cancel_job(cfg, "k", "job-9")
    busy = await be.busy(cfg, "k", start, start + timedelta(1))
    assert len(busy) == 1
    # every request carried a valid signature over "<ts>.<body>"
    for req in seen:
        ts = req.headers[TIMESTAMP_HEADER]
        assert verify("k", ts, req.headers.get(SIGNATURE_HEADER), req.content)
    post = next(r for r in seen if r.method == "POST")
    assert json.loads(post.content)["booking_id"] == "bk-1"
    assert seen[1].url.params["area"] == "M1"
    # no URL configured → clear error, no HTTP
    with pytest.raises(ValueError):
        await be.resources(SchedulerConfig(tenant_id="d", provider=SchedulerProvider.WEBHOOK), "k")


async def test_servicem8_scheduler_mocked() -> None:
    calls: list[tuple[str, str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Api-Key"] == "sm8-key"
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        p = req.url.path
        if p.endswith("/staff.json"):
            return httpx.Response(
                200, json=[{"uuid": "st-1", "first": "Ann", "last": "Lee", "active": 1}]
            )
        if p.endswith("/jobactivity.json") and req.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "start_date": "2026-10-01 09:00:00",
                        "end_date": "2026-10-01 10:00:00",
                        "staff_uuid": "st-1",
                    }
                ],
            )
        if p.endswith("/company.json") and req.method == "GET":
            return httpx.Response(200, json=[])
        if p.endswith("/company.json"):
            return httpx.Response(200, headers={"x-record-uuid": "co-1"})
        if p.endswith("/companycontact.json"):
            return httpx.Response(200)
        if p.endswith("/job.json"):
            return httpx.Response(200, headers={"x-record-uuid": "job-1"})
        if p.endswith("/jobactivity.json"):
            return httpx.Response(200, headers={"x-record-uuid": "act-1"})
        if "/jobactivity/act-1.json" in p or "/job/job-1.json" in p:
            return httpx.Response(200)
        return httpx.Response(404)

    be = ServiceM8Scheduler(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    cfg = SchedulerConfig(tenant_id="demo", provider=SchedulerProvider.SERVICEM8)
    staff = await be.resources(cfg, "sm8-key")
    assert staff[0].name == "Ann Lee" and staff[0].id == "st-1"
    start = datetime(2026, 10, 1, tzinfo=UTC)  # a Thursday
    shifts = await be.shifts(cfg, "sm8-key", start, start + timedelta(days=3))
    assert len(shifts) == 2  # Thu + Fri, weekend skipped
    slots = await be.availability(
        cfg, "sm8-key", start=start, end=start + timedelta(1), minutes=60, service=None, area=None
    )
    assert slots and all(
        not (s.start < start + timedelta(hours=10) and s.end > start + timedelta(hours=9))
        for s in slots
    )
    job = ExternalJob(
        start=start + timedelta(hours=10),
        end=start + timedelta(hours=11),
        customer="Sam Jones",
        phone="+447700900123",
        address="1 Piccadilly, M1 1AA",
        notes="Boiler leaking",
        service="Repair",
        minutes=60,
        resource_id="st-1",
        booking_id="bk-1",
    )
    ref = await be.create_job(cfg, "sm8-key", job)
    assert ref == "job-1:act-1"
    job_post = next(b for m, p, b in calls if m == "POST" and p.endswith("/job.json"))
    assert (
        "Boiler leaking" in job_post["job_description"]
        and job_post["job_address"] == "1 Piccadilly, M1 1AA"
    )
    act = next(b for m, p, b in calls if m == "POST" and p.endswith("/jobactivity.json"))
    assert act["staff_uuid"] == "st-1" and act["start_date"] == "2026-10-01 10:00:00"
    await be.update_job(cfg, "sm8-key", ref, job)
    await be.cancel_job(cfg, "sm8-key", ref)
    assert ("POST", "/api_1.0/job/job-1.json", {"status": "Cancelled"}) in calls
    with pytest.raises(ValueError):
        await be.update_job(cfg, "sm8-key", "job-only", job)


async def test_scheduler_mode_books_into_tool_and_syncs_events(client: AsyncClient) -> None:
    r = await client.put(
        "/v1/team/scheduler",
        params=T,
        json={"provider": "simulated", "name": "Sim tool", "secret": "hook-secret"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["has_secret"] is True and "secret" not in r.json()
    r = await client.post("/v1/team/scheduler/test", params={**T, "import_staff": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and len(r.json()["staff"]) == 2 and r.json()["imported"] == 2
    r = await client.get("/v1/team/resources", params=T)
    assert sorted(x["external_ref"] for x in r.json()) == ["ext-1", "ext-2"]
    await client.put(
        "/v1/team/settings",
        params=T,
        json={"policy": "least_loaded", "mode": "scheduler", "emergency_to_on_call": True},
    )
    r = await client.get(
        "/v1/worker/calendar/availability", params={**T, "days": 7}, headers=HEADERS
    )
    assert r.status_code == 200, r.text
    avail = r.json()
    assert avail["source"] == "scheduler" and avail["slots"]
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params=T,
        headers=HEADERS,
        json={"start": avail["slots"][0]["start"], "name": "Sam", "phone": "+447700900123"},
    )
    assert r.status_code == 201, r.text
    booking = r.json()
    assert booking["source"] == "scheduler" and booking["provider_ref"]

    # dashboard schedule is read-only while the tool owns the diary
    today = datetime.now(UTC).date().isoformat()
    r = await client.get("/v1/team/schedule", params={**T, "start": today, "days": 7})
    assert r.status_code == 200, r.text
    assert r.json()["read_only"] is True and r.json()["source"] == "scheduler"
    r = await client.post(f"/v1/team/bookings/{booking['id']}/cancel", params=T)
    assert r.status_code == 423

    # inbound events must be signed
    body = json.dumps({"event": "job.cancelled", "ref": booking["provider_ref"]}).encode()
    r = await client.post("/v1/public/scheduler/events/demo", content=body)
    assert r.status_code == 401
    ts = str(int(time.time()))
    hdr = {
        TIMESTAMP_HEADER: ts,
        SIGNATURE_HEADER: sign("wrong", ts, body),
        "content-type": "application/json",
    }
    r = await client.post("/v1/public/scheduler/events/demo", content=body, headers=hdr)
    assert r.status_code == 401
    hdr[SIGNATURE_HEADER] = sign("hook-secret", ts, body)
    r = await client.post("/v1/public/scheduler/events/demo", content=body, headers=hdr)
    assert r.status_code == 202, r.text
    r = await client.get(f"/v1/team/bookings/{booking['id']}", params=T)
    assert r.json()["status"] == "cancelled"
    # unknown tenant → 404
    r = await client.post("/v1/public/scheduler/events/nobody", content=body, headers=hdr)
    assert r.status_code == 404


# -- 22d: schedule read model ----------------------------------------------------------------------


async def test_schedule_view_lanes_blocks_and_filters(client: AsyncClient) -> None:
    await _diary(client, services=[{"id": "svc-rep", "name": "Repair", "minutes": 60}])
    r = await client.post(
        "/v1/team/resources",
        params=T,
        json=_res(
            "Ann",
            skills=["Repair"],
            site_id="site-a",
            hours=_weekday_hours(8, 16),
            calendar_id="ann",
        ),
    )
    ann = r.json()
    r = await client.post(
        "/v1/team/resources",
        params=T,
        json=_res("Bob", skills=["Tiling"], site_id="site-b", calendar_id="bob"),
    )
    bob = r.json()
    r = await client.get(
        "/v1/worker/calendar/availability",
        params={**T, "days": 7, "service_id": "svc-rep"},
        headers=HEADERS,
    )
    slot = r.json()["slots"][0]["start"]
    r = await client.post(
        "/v1/worker/calendar/bookings",
        params=T,
        headers=HEADERS,
        json={
            "start": slot,
            "name": "Sam",
            "service_id": "svc-rep",
            "address": "M1 1AA",
            "phone": "+447700900123",
        },
    )
    assert r.status_code == 201, r.text
    booking = r.json()
    assert booking["resource_id"] == ann["id"]
    day = slot[:10]

    r = await client.get("/v1/team/schedule", params={**T, "start": day, "days": 1})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["days"] == 1 and view["read_only"] is False and view["source"] == "calendar"
    lanes = {ln["name"]: ln for ln in view["lanes"]}
    assert set(lanes) == {"Ann", "Bob"}
    blocks = [b for b in lanes["Ann"]["blocks"] if b["kind"] == "booking"]
    assert len(blocks) == 1
    blk = blocks[0]
    assert blk["customer"] == "Sam" and blk["service"] == "Repair" and blk["area"] == "M1"
    assert blk["assignee"] == "Ann" and blk["booking_id"] == booking["id"] and blk["status"]
    assert lanes["Ann"]["shifts"] and lanes["Ann"]["shift_minutes"] == 8 * 60
    assert lanes["Ann"]["booked_minutes"] == 60
    assert abs(lanes["Ann"]["utilisation"] - 60 / 480) < 1e-6
    assert lanes["Bob"]["booked_minutes"] == 0 and not [
        b for b in lanes["Bob"]["blocks"] if b["kind"] == "booking"
    ]

    # week view normalises odd day counts to 7 and is cached briefly
    r = await client.get("/v1/team/schedule", params={**T, "start": day, "days": 5})
    assert r.json()["days"] == 7
    r = await client.get("/v1/team/schedule", params={**T, "start": day, "days": 7})
    assert r.json()["cached"] is True
    r = await client.get(
        "/v1/team/schedule", params={**T, "start": day, "days": 7, "refresh": "true"}
    )
    assert r.json()["cached"] is False

    # filters
    r = await client.get("/v1/team/schedule", params={**T, "start": day, "site_id": "site-b"})
    assert [ln["name"] for ln in r.json()["lanes"]] == ["Bob"]
    r = await client.get("/v1/team/schedule", params={**T, "start": day, "resource_id": bob["id"]})
    assert [ln["name"] for ln in r.json()["lanes"]] == ["Bob"]
    r = await client.get("/v1/team/schedule", params={**T, "start": day, "service_id": "svc-rep"})
    assert [ln["name"] for ln in r.json()["lanes"]] == ["Ann"]

    # cancelled bookings keep their block with the new status
    await client.post(f"/v1/team/bookings/{booking['id']}/cancel", params=T)
    r = await client.get("/v1/team/schedule", params={**T, "start": day, "refresh": "true"})
    ann_lane = next(ln for ln in r.json()["lanes"] if ln["name"] == "Ann")
    blk = next(b for b in ann_lane["blocks"] if b["kind"] == "booking")
    assert blk["status"] == "cancelled" and ann_lane["booked_minutes"] == 0

    # other tenants can't read it
    r = await client.get("/v1/team/schedule", params={"tenant_id": "other", "start": day})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert r.json()["lanes"] == [] or all(not ln["blocks"] for ln in r.json()["lanes"])


async def test_schedule_single_lane_without_resources(client: AsyncClient) -> None:
    await _diary(client)
    r = await client.get(
        "/v1/worker/calendar/availability", params={**T, "days": 7}, headers=HEADERS
    )
    slot = r.json()["slots"][0]["start"]
    await client.post(
        "/v1/worker/calendar/bookings",
        params=T,
        headers=HEADERS,
        json={"start": slot, "name": "Solo"},
    )
    r = await client.get("/v1/team/schedule", params={**T, "start": slot[:10]})
    assert r.status_code == 200, r.text
    lanes = r.json()["lanes"]
    assert len(lanes) == 1 and lanes[0]["resource_id"] is None
    assert any(b["kind"] == "booking" and b["customer"] == "Solo" for b in lanes[0]["blocks"])
