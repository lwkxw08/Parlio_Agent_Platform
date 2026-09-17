"""Phase 20f/g/i: after-hours personas, multi-location sites, transcript & recording search."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from httpx import AsyncClient

from parlio_api.auth import DEV_TENANT
from parlio_api.search import hit_for
from parlio_api.sites import Site, rollup, site_of
from parlio_api.store import CallFilter, CallRecord, search_terms
from parlio_voice.models import (
    AfterHoursPersona,
    AssistantConfig,
    CallEvent,
    CallEventType,
    DayHours,
    Destination,
    Holiday,
    Schedule,
    SiteRef,
    TransferConfig,
    TransferWhenClosed,
)

HEADERS = {"X-Worker-Key": "dev-worker-key"}

# Mon 2026-03-02 .. ; Europe/London is GMT in March until the 29th
MON_10 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
MON_20 = datetime(2026, 3, 2, 20, 0, tzinfo=UTC)
SAT_11 = datetime(2026, 3, 7, 11, 0, tzinfo=UTC)
XMAS_11 = datetime(2026, 12, 25, 11, 0, tzinfo=UTC)
XMAS_EVE_11 = datetime(2026, 12, 24, 11, 0, tzinfo=UTC)
XMAS_EVE_14 = datetime(2026, 12, 24, 14, 0, tzinfo=UTC)


def _cfg(**kw: Any) -> AssistantConfig:
    base: dict[str, Any] = {
        "tenant_id": "t1",
        "company_id": "t1",
        "assistant_id": "a1",
        "name": "Ava",
        "business_name": "Acme Plumbing",
        "hours": Schedule(
            holidays=[
                Holiday(day=date(2026, 12, 25), name="Christmas Day"),
                Holiday(
                    day=date(2026, 12, 24),
                    name="Christmas Eve",
                    closed=False,
                    hours=DayHours(open=time(9, 0), close=time(13, 0)),
                ),
            ]
        ),
    }
    base.update(kw)
    return AssistantConfig(**base)


# -- 20f: windows, holidays, next opening ---------------------------------------------------------


def test_window_selection_and_holidays() -> None:
    cfg = _cfg()
    assert cfg.window(MON_10) == "open"
    assert cfg.window(MON_20) == "closed"
    assert cfg.window(SAT_11) == "closed"
    assert cfg.window(XMAS_11) == "holiday"  # closed all day
    assert cfg.window(XMAS_EVE_11) == "open"  # reduced hours 09-13
    assert cfg.window(XMAS_EVE_14) == "holiday"


def test_next_opening() -> None:
    cfg = _cfg()
    assert cfg.next_opening(MON_20) == "tomorrow at 09:00"
    assert cfg.next_opening(SAT_11) == "on Monday at 09:00"
    assert cfg.next_opening(datetime(2026, 3, 2, 7, 0, tzinfo=UTC)) == "today at 09:00"
    # Christmas Day closed -> Boxing Day is a Saturday -> Monday 28th
    assert cfg.next_opening(XMAS_11) == "on Monday at 09:00"
    # Christmas Eve after the reduced window -> next is Christmas Day (closed) -> Monday
    assert cfg.next_opening(XMAS_EVE_14) == "on Monday at 09:00"
    always = _cfg(hours=Schedule(always=True))
    assert always.next_opening(SAT_11) is None


def test_after_hours_persona_greeting_and_prompt() -> None:
    cfg = _cfg(
        after_hours=AfterHoursPersona(
            enabled=True,
            greeting="Hi, {business_name} is closed right now but I can take a message.",
            holiday_greeting="Happy holidays from {business_name}!",
            tone="calm and brief",
            intake_only=True,
            transfer=TransferWhenClosed.NEVER,
        )
    )
    assert "Acme Plumbing" in cfg.rendered_greeting(MON_10)
    assert "closed right now" not in cfg.rendered_greeting(MON_10)
    assert cfg.rendered_greeting(MON_20).startswith("Hi, Acme Plumbing is closed")
    assert cfg.rendered_greeting(XMAS_11) == "Happy holidays from Acme Plumbing!"
    prompt = cfg.rendered_instructions(MON_20)
    assert "After-hours behaviour" in prompt
    assert "reopen tomorrow at 09:00" in prompt
    assert "calm and brief" in prompt
    assert "Never offer or attempt a transfer" in prompt
    assert "create_ticket" in prompt
    # daytime prompt carries none of it
    assert "After-hours behaviour" not in cfg.rendered_instructions(MON_10)


def test_after_hours_disabled_is_backwards_compatible() -> None:
    """Assistants saved before 20f (no `after_hours`, no holidays) behave exactly as before."""
    legacy = {
        "tenant_id": "t1",
        "company_id": "t1",
        "assistant_id": "a1",
        "name": "Ava",
        "business_name": "Acme",
        "hours": {"timezone": "Europe/London", "hours": {"mon": {}}, "always": False},
    }
    cfg = AssistantConfig.model_validate(legacy)
    assert cfg.after_hours.enabled is False
    assert cfg.hours.holidays == []
    assert cfg.sites == []
    assert cfg.rendered_greeting(SAT_11) == cfg.rendered_greeting(MON_10)
    assert cfg.after_hours_active(SAT_11) is False
    assert "After-hours behaviour" not in cfg.rendered_instructions(SAT_11)
    assert cfg.is_open(SAT_11) is False  # hours checks unchanged


def test_after_hours_prompt_variants() -> None:
    p = AfterHoursPersona(enabled=True, transfer=TransferWhenClosed.ON_CALL_ONLY)
    assert "on-call" in p.prompt("closed", None)
    assert "holiday" in AfterHoursPersona(enabled=True).prompt("holiday", None)
    quiet = AfterHoursPersona(enabled=True, quote_next_opening=False)
    assert "reopen" not in quiet.prompt("closed", "tomorrow at 09:00")


# -- 20g: sites, number matching, site-aware transfers ---------------------------------------


def _sites_cfg() -> AssistantConfig:
    return _cfg(
        sites=[
            SiteRef(id="leeds", name="Leeds", brand_name="Acme Leeds", numbers=["+441130001"]),
            SiteRef(id="york", name="York", numbers=["+441904000"], address="1 Shambles"),
        ],
        transfer=TransferConfig(
            destinations=[
                Destination(
                    id="d1", name="Leeds desk", department="sales", site_id="leeds", address="+1"
                ),
                Destination(
                    id="d2", name="York desk", department="sales", site_id="york", address="+2"
                ),
                Destination(id="d3", name="Head office", department="accounts", address="+3"),
            ]
        ),
    )


def test_site_number_matching_and_brand() -> None:
    cfg = _sites_cfg()
    assert cfg.site_for("+441130001") is not None
    assert cfg.site_for("441130001").id == "leeds"  # digits-only from the SIP header
    assert cfg.site_for("+447000000") is None
    assert cfg.site_for(None) is None
    leeds = cfg.site_for("+441130001")
    york = cfg.site_for("+441904000")
    assert "Acme Leeds" in cfg.rendered_greeting(MON_10, site=leeds)
    assert "Acme Plumbing" in cfg.rendered_greeting(MON_10, site=york)  # no brand -> business
    ins = cfg.rendered_instructions(MON_10, site=york)
    assert "number for York" in ins and "1 Shambles" in ins


def test_site_aware_transfer_candidates() -> None:
    cfg = _sites_cfg()
    names = lambda site: [  # noqa: E731
        d.name for d in cfg.transfer.candidates("sales", MON_10, False, site_id=site)
    ]
    assert names("leeds") == ["Leeds desk"]
    assert names("york") == ["York desk"]
    assert names(None) == ["Leeds desk", "York desk"]  # no site: unchanged behaviour
    # generic destinations stay available for every site
    accounts = [d.name for d in cfg.transfer.candidates("accounts", MON_10, False, site_id="leeds")]
    assert accounts == ["Head office"]


def _call(call_id: str, dialed: str | None, **kw: Any) -> CallRecord:
    base: dict[str, Any] = {
        "call_id": call_id,
        "tenant_id": "t1",
        "company_id": "t1",
        "assistant_id": "a1",
        "caller": "+447700900000",
        "dialed": dialed,
        "status": "completed",
        "started_at": MON_10,
        "answered_at": MON_10,
        "duration_s": 60.0,
    }
    base.update(kw)
    return CallRecord(**base)


def test_site_filter_and_rollup() -> None:
    cfg = _sites_cfg()
    sites = [Site(**s.model_dump(), assistant_id="a1") for s in cfg.sites]
    calls = [
        _call("c1", "+441130001"),
        _call("c2", "441130001", answered_at=None, duration_s=None),  # missed, digits-only
        _call("c3", "+441904000"),
        _call("c4", "+441000000"),  # number no site owns
        _call("c5", "+441130001", direction="outbound"),  # our caller id; not a site call
    ]
    assert site_of(calls[0], sites) is not None
    assert site_of(calls[4], sites) is None
    f = CallFilter(tenant_id="t1", site_numbers=["441130001"])
    assert [c.call_id for c in calls if f.matches(c)] == ["c1", "c2"]
    r = rollup(calls, sites, [cfg], days=30, now=MON_20)
    by = {s.site_id: s for s in r.sites}
    assert by["leeds"].calls == 2 and by["leeds"].missed == 1 and by["leeds"].answered == 1
    assert by["york"].calls == 1
    assert by[None].calls == 1 and r.unassigned_numbers == ["+441000000"]
    assert r.total_calls == 4
    assert r.sites[0].site_id == "leeds"  # busiest first, unassigned last
    assert r.sites[-1].site_id is None


# -- 20i: search ---------------------------------------------------------------------------------


def test_search_terms_and_hit_moments() -> None:
    assert search_terms("Boilers leaking") == ["boiler", "leaking"]
    t0 = datetime(2026, 3, 2, 10, 0, 0, tzinfo=UTC)
    c = _call(
        "c1",
        "+441130001",
        answered_at=t0,
        recordings=["rec/c1.ogg"],
        summary="Caller reported a leaking boiler.",
        transcript=[
            {"role": "assistant", "text": "Hello", "at": t0.isoformat()},
            {
                "role": "user",
                "text": "My boiler is leaking all over the kitchen floor",
                "at": datetime(2026, 3, 2, 10, 0, 42, tzinfo=UTC).isoformat(),
            },
            {"role": "assistant", "text": "Sorry to hear that", "at": None},
        ],
    )
    hit = hit_for(c, "boiler", [])
    assert hit.summary_matched is True
    assert hit.total_matches == 2
    assert len(hit.moments) == 1
    m = hit.moments[0]
    assert m.seq == 1 and m.role == "user" and m.offset_s == 42.0 and m.recording_index == 0
    # no recording -> no seek target, but the moment is still returned
    hit2 = hit_for(c.model_copy(update={"recordings": []}), "boiler", [])
    assert hit2.moments[0].recording_index is None and hit2.moments[0].offset_s == 42.0


# -- API: sites routes, search route, tenant isolation ---------------------------------------


def ev(t: CallEventType, call_id: str, payload: dict[str, Any], tenant: str = DEV_TENANT) -> dict:
    return CallEvent(
        type=t,
        call_id=call_id,
        tenant_id=tenant,
        company_id=tenant,
        assistant_id=tenant,
        payload=payload,
    ).model_dump(mode="json")


async def _post_call(
    client: AsyncClient, call_id: str, dialed: str, lines: list[str], *, tenant: str = DEV_TENANT
) -> None:
    events = [
        ev(
            CallEventType.CALL_STARTED,
            call_id,
            {"caller": "+447700900123", "dialed": dialed},
            tenant,
        ),
        ev(CallEventType.CALL_ANSWERED, call_id, {"answer_latency_s": 0.3}, tenant),
        ev(CallEventType.RECORDING_STARTED, call_id, {"keys": [f"rec/{call_id}.ogg"]}, tenant),
        *[
            ev(CallEventType.TRANSCRIPT_ITEM, call_id, {"role": "user", "text": line}, tenant)
            for line in lines
        ],
        ev(CallEventType.CALL_ENDED, call_id, {"reason": "hangup", "duration_s": 40}, tenant),
    ]
    for e in events:
        r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
        assert r.status_code == 202, r.text


@pytest.mark.anyio
async def test_sites_api_and_isolation(client: AsyncClient) -> None:
    r = await client.get("/v1/sites")
    assert r.status_code == 200 and r.json() == []
    body = [
        {"id": "leeds", "name": "Leeds", "brand_name": "Demo Leeds", "numbers": ["+441130001"]},
        {"id": "york", "name": "York", "numbers": ["+441904000"]},
    ]
    r = await client.put(f"/v1/assistants/{DEV_TENANT}/sites", json=body)
    assert r.status_code == 200, r.text
    assert [s["id"] for s in r.json()["sites"]] == ["leeds", "york"]
    r = await client.get("/v1/sites")
    assert [s["id"] for s in r.json()] == ["leeds", "york"]
    assert r.json()[0]["assistant_id"] == DEV_TENANT

    # a number cannot belong to two sites; ids must be unique
    dup = [*body, {"id": "hull", "name": "Hull", "numbers": ["441130001"]}]
    assert (await client.put(f"/v1/assistants/{DEV_TENANT}/sites", json=dup)).status_code == 422
    same = [body[0], {**body[1], "id": "leeds"}]
    assert (await client.put(f"/v1/assistants/{DEV_TENANT}/sites", json=same)).status_code == 422

    # strangers cannot read or write another organisation's sites
    other = {"X-Parlio-User": "stranger@example.com"}
    assert (await client.get(f"/v1/sites?tenant_id={DEV_TENANT}", headers=other)).status_code == 403
    r = await client.put(f"/v1/assistants/{DEV_TENANT}/sites", json=body, headers=other)
    assert r.status_code == 403
    r = await client.get(f"/v1/analytics/sites?tenant_id={DEV_TENANT}", headers=other)
    assert r.status_code == 403

    # calls route to sites by dialled number; filter + roll-up follow
    await _post_call(client, "s1", "+441130001", ["My boiler is leaking"])
    await _post_call(client, "s2", "+441904000", ["Can I book a service"])
    await _post_call(client, "s3", "+441000000", ["Wrong number sorry"])
    r = await client.get(f"/v1/calls?tenant_id={DEV_TENANT}&site=leeds")
    assert [c["call_id"] for c in r.json()] == ["s1"]
    r = await client.get(f"/v1/calls?tenant_id={DEV_TENANT}&site=nowhere")
    assert r.json() == []
    r = await client.get(f"/v1/analytics/sites?tenant_id={DEV_TENANT}")
    assert r.status_code == 200
    roll = r.json()
    assert roll["total_calls"] == 3
    by = {s["site_id"]: s for s in roll["sites"]}
    assert by["leeds"]["calls"] == 1 and by["york"]["calls"] == 1 and by[None]["calls"] == 1
    assert roll["unassigned_numbers"] == ["+441000000"]
    r = await client.get(f"/v1/analytics/overview?tenant_id={DEV_TENANT}&site=york")
    assert r.status_code == 200 and r.json()["current"]["total_calls"] == 1


@pytest.mark.anyio
async def test_transcript_search_api(client: AsyncClient) -> None:
    await client.put(
        f"/v1/assistants/{DEV_TENANT}/sites",
        json=[{"id": "leeds", "name": "Leeds", "numbers": ["+441130001"]}],
    )
    await _post_call(
        client, "q1", "+441130001", ["Hello", "My boiler is leaking all over the floor"]
    )
    await _post_call(client, "q2", "+441904000", ["I need a new radiator fitted"])
    await _post_call(client, "q3", "+441904000", ["The boilers at both flats need servicing"])

    r = await client.get(f"/v1/calls/search?tenant_id={DEV_TENANT}&q=boiler")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["engine"] in ("memory", "postgres_fts")
    ids = {h["call_id"] for h in res["hits"]}
    assert ids == {"q1", "q3"}
    q1 = next(h for h in res["hits"] if h["call_id"] == "q1")
    assert q1["site_id"] == "leeds" and q1["recordings"] == 1
    assert q1["moments"][0]["role"] == "user"
    assert "boiler" in q1["moments"][0]["snippet"]
    assert q1["moments"][0]["offset_s"] is not None
    assert q1["moments"][0]["recording_index"] == 0

    # site filter narrows the search
    r = await client.get(f"/v1/calls/search?tenant_id={DEV_TENANT}&q=boiler&site=leeds")
    assert [h["call_id"] for h in r.json()["hits"]] == ["q1"]
    # multi-word: every word must appear
    r = await client.get(f"/v1/calls/search?tenant_id={DEV_TENANT}&q=boiler+radiator")
    assert r.json()["hits"] == []
    r = await client.get(f"/v1/calls/search?tenant_id={DEV_TENANT}&q=radiator+fitted")
    assert [h["call_id"] for h in r.json()["hits"]] == ["q2"]
    # too short / missing
    assert (await client.get(f"/v1/calls/search?tenant_id={DEV_TENANT}&q=b")).status_code == 422

    # another organisation's calls are invisible, and strangers get 403
    await _post_call(client, "x1", "+449999", ["boiler boiler boiler"], tenant="other-org")
    r = await client.get(f"/v1/calls/search?tenant_id={DEV_TENANT}&q=boiler")
    assert "x1" not in {h["call_id"] for h in r.json()["hits"]}
    r = await client.get(
        f"/v1/calls/search?tenant_id={DEV_TENANT}&q=boiler",
        headers={"X-Parlio-User": "stranger@example.com"},
    )
    assert r.status_code == 403
