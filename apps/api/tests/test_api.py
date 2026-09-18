from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from parlio_api.db.postgres import PostgresStore
from parlio_api.postcall import PostCallProcessor
from parlio_voice.models import AssistantConfig, CallEvent, CallEventType

HEADERS = {"X-Worker-Key": "dev-worker-key"}


def ev(
    t: CallEventType, call_id: str, payload: dict[str, Any], tenant: str = "demo"
) -> dict[str, Any]:
    return CallEvent(
        type=t,
        call_id=call_id,
        tenant_id=tenant,
        company_id=tenant,
        assistant_id="demo",
        payload=payload,
    ).model_dump(mode="json")


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_resolve_requires_worker_key(client: AsyncClient) -> None:
    r = await client.get("/v1/worker/assistants/resolve", params={"number": "+440000000000"})
    assert r.status_code == 401


async def test_resolve_demo_number(client: AsyncClient) -> None:
    r = await client.get(
        "/v1/worker/assistants/resolve", params={"number": "+440000000000"}, headers=HEADERS
    )
    assert r.status_code == 200
    assert AssistantConfig.model_validate(r.json()).assistant_id == "demo"


async def test_resolve_unknown_number_404(client: AsyncClient) -> None:
    r = await client.get(
        "/v1/worker/assistants/resolve", params={"number": "+441111"}, headers=HEADERS
    )
    assert r.status_code == 404


async def test_upsert_assistant_and_resolve(client: AsyncClient) -> None:
    cfg = AssistantConfig(
        tenant_id="demo",
        company_id="c1",
        assistant_id="a1",
        business_name="Bright Sparks Electrical",
    )
    r = await client.put(
        "/v1/assistants/a1",
        json={"config": cfg.model_dump(mode="json"), "numbers": ["+441612345678"]},
    )
    assert r.status_code == 200
    r = await client.get(
        "/v1/worker/assistants/resolve", params={"number": "+441612345678"}, headers=HEADERS
    )
    assert r.json()["business_name"] == "Bright Sparks"


async def test_call_lifecycle_events_build_call_record(client: AsyncClient) -> None:
    c = "call-1"
    for e in [
        ev(CallEventType.CALL_STARTED, c, {"caller": "+447700900000", "dialed": "+440000000000"}),
        ev(CallEventType.CALL_ANSWERED, c, {"answer_latency_s": 0.41}),
        ev(CallEventType.TRANSCRIPT_ITEM, c, {"role": "assistant", "text": "Hello"}),
        ev(
            CallEventType.CALL_ENDED,
            c,
            {"reason": "hangup", "duration_s": 42.0, "latency": {"p50_s": 0.45}},
        ),
    ]:
        r = await client.post("/v1/worker/events", json=e, headers=HEADERS)
        assert r.status_code == 202, r.text

    r = await client.get("/v1/calls/call-1")
    call = r.json()
    assert call["status"] == "completed"
    assert call["caller"] == "+447700900000"
    assert call["answer_latency_s"] == 0.41
    assert call["duration_s"] == 42.0
    assert call["latency"]["p50_s"] == 0.45
    assert len(call["transcript"]) == 1

    r = await client.get("/v1/calls", params={"tenant_id": "demo"})
    assert [c["call_id"] for c in r.json()] == ["call-1"]


async def test_events_are_idempotent(client: AsyncClient) -> None:
    e = CallEvent(
        type=CallEventType.CALL_STARTED,
        call_id="c2",
        tenant_id="d",
        company_id="d",
        assistant_id="d",
    ).model_dump(mode="json")
    r1 = await client.post("/v1/worker/events", json=e, headers=HEADERS)
    r2 = await client.post("/v1/worker/events", json=e, headers=HEADERS)
    assert r1.json()["applied"] is True
    assert r2.json()["applied"] is False


async def test_postcall_pipeline_enriches_call(client: AsyncClient, app: FastAPI) -> None:
    r = await client.put(
        "/v1/assistants/demo/required-fields",
        json=[{"name": "name"}, {"name": "email"}, {"name": "postcode"}],
    )
    assert r.status_code == 200, r.text

    async def run_call(call_id: str) -> dict[str, Any]:
        for e in [
            ev(CallEventType.CALL_STARTED, call_id, {"caller": "+447700900123"}),
            ev(CallEventType.CALL_ANSWERED, call_id, {}),
            ev(
                CallEventType.CALL_ENDED,
                call_id,
                {
                    "reason": "hangup",
                    "transcript": [
                        {"role": "assistant", "text": "Hello, how can I help?"},
                        {
                            "role": "user",
                            "text": "Hi, my name is Jane Smith, email jane@example.com",
                        },
                    ],
                },
            ),
        ]:
            assert (
                await client.post("/v1/worker/events", json=e, headers=HEADERS)
            ).status_code == 202
        proc: PostCallProcessor = app.state.postcall
        await proc.drain()
        return dict((await client.get(f"/v1/calls/{call_id}")).json())

    first = await run_call("pc-1")
    assert first["caller_type"] == "new"
    assert first["extracted"] == {"name": "Jane Smith", "email": "jane@example.com"}
    assert first["missed_fields"] == ["postcode"]
    assert "Jane Smith" in first["summary"]
    assert first["contact_id"]

    second = await run_call("pc-2")
    assert second["caller_type"] == "returning"
    assert second["contact_id"] == first["contact_id"]


async def test_issued_worker_key_is_accepted(client: AsyncClient) -> None:
    r = await client.post("/v1/worker-keys", json={"tenant_id": "demo", "name": "vps-1"})
    assert r.status_code == 201
    key = r.json()["key"]
    assert key.startswith("pk_")
    ok = await client.get(
        "/v1/worker/assistants/resolve",
        params={"number": "+440000000000"},
        headers={"X-Worker-Key": key},
    )
    assert ok.status_code == 200
    bad = await client.get(
        "/v1/worker/assistants/resolve",
        params={"number": "+440000000000"},
        headers={"X-Worker-Key": "pk_nope"},
    )
    assert bad.status_code == 401


async def test_calls_are_tenant_scoped(client: AsyncClient) -> None:
    for tenant, cid in (("t-a", "a-1"), ("t-b", "b-1")):
        e = ev(CallEventType.CALL_STARTED, cid, {"caller": "+447700900001"}, tenant=tenant)
        await client.post("/v1/worker/events", json=e, headers=HEADERS)
    e = ev(CallEventType.CALL_STARTED, "d-1", {"caller": "+447700900001"})
    await client.post("/v1/worker/events", json=e, headers=HEADERS)
    # the dev owner belongs to "demo" only: other tenants are forbidden, and an
    # unqualified query resolves to the caller's own organisation, never to "all"
    r = await client.get("/v1/calls", params={"tenant_id": "t-a"})
    assert r.status_code == 403
    r = await client.get("/v1/calls")
    assert [c["call_id"] for c in r.json()] == ["d-1"]


async def test_user_without_organisation_sees_nothing(client: AsyncClient) -> None:
    hdr = {"X-Parlio-User": "nobody@example.com"}
    for path in ("/v1/assistants", "/v1/calls", "/v1/tickets", "/v1/transfers"):
        r = await client.get(path, headers=hdr)
        assert r.status_code == 403, path
    r = await client.get("/v1/assistants/demo/versions", headers=hdr)
    assert r.status_code == 403
    r = await client.get("/v1/calls", params={"tenant_id": "demo"}, headers=hdr)
    assert r.status_code == 403


async def test_rls_blocks_cross_tenant_rows(
    client: AsyncClient, app: FastAPI, backend: str
) -> None:
    """Even a query with no WHERE clause only sees the tenant set on the session."""
    if backend != "postgres":
        pytest.skip("RLS is a Postgres feature")
    for tenant, cid in (("t-a", "a-1"), ("t-b", "b-1")):
        e = ev(CallEventType.CALL_STARTED, cid, {}, tenant=tenant)
        await client.post("/v1/worker/events", json=e, headers=HEADERS)
    store: PostgresStore = app.state.store
    engine = store._engine
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', 't-b', true)"))
        ids = (await conn.execute(text("SELECT id FROM calls"))).scalars().all()
        assert ids == ["b-1"]
        # writes for another tenant are rejected by the WITH CHECK clause
        with pytest.raises(Exception, match="row-level security"):
            await conn.execute(
                text(
                    "INSERT INTO contacts (id, organization_id, company_id, e164)"
                    " VALUES ('x', 't-a', 't-a', '+44')"
                )
            )
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT calls FROM analytics_calls_daily"))).all()
        assert len(rows) == 2
        parts = (
            await conn.execute(
                text("SELECT count(*) FROM pg_inherits WHERE inhparent = 'calls'::regclass")
            )
        ).scalar_one()
        assert parts >= 5
