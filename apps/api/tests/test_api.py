import pytest
from httpx import ASGITransport, AsyncClient

from parlio_api.main import create_app
from parlio_api.settings import get_settings
from parlio_voice.models import AssistantConfig, CallEvent, CallEventType

HEADERS = {"X-Worker-Key": "dev-worker-key"}


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PARLIO_REDIS_URL", "")
    get_settings.cache_clear()
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c
    get_settings.cache_clear()


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
        tenant_id="t1", company_id="c1", assistant_id="a1", business_name="Bright Sparks Electrical"
    )
    r = await client.put(
        "/v1/assistants/a1",
        json={"config": cfg.model_dump(mode="json"), "numbers": ["+441612345678"]},
    )
    assert r.status_code == 200
    r = await client.get(
        "/v1/worker/assistants/resolve", params={"number": "+441612345678"}, headers=HEADERS
    )
    assert r.json()["business_name"] == "Bright Sparks Electrical"


async def test_call_lifecycle_events_build_call_record(client: AsyncClient) -> None:
    def ev(t: CallEventType, payload: dict) -> dict:  # type: ignore[type-arg]
        return CallEvent(
            type=t,
            call_id="call-1",
            tenant_id="demo",
            company_id="demo",
            assistant_id="demo",
            payload=payload,
        ).model_dump(mode="json")

    for e in [
        ev(CallEventType.CALL_STARTED, {"caller": "+447700900000", "dialed": "+440000000000"}),
        ev(CallEventType.CALL_ANSWERED, {"answer_latency_s": 0.41}),
        ev(CallEventType.TRANSCRIPT_ITEM, {"role": "assistant", "text": "Hello"}),
        ev(
            CallEventType.CALL_ENDED,
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
