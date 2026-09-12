import httpx

from parlio_api.postcall import HeuristicAnalyser, OpenAIAnalyser, process_call
from parlio_api.store import CallRecord, MemoryStore, RequiredField
from parlio_voice.models import CallEvent, CallEventType


def _call(*user_lines: str, caller: str = "+447700900001") -> CallRecord:
    return CallRecord(
        call_id="c",
        tenant_id="t",
        company_id="co",
        assistant_id="a",
        caller=caller,
        transcript=[{"role": "user", "text": t} for t in user_lines],
    )


async def test_heuristic_extracts_common_fields() -> None:
    a = await HeuristicAnalyser().analyse(
        _call("Hi, I'm Tom Baker, call me back on 07700 900 123 or tom@baker.co.uk"),
        [RequiredField(name="name"), RequiredField(name="phone"), RequiredField(name="email")],
    )
    assert a.extracted == {
        "email": "tom@baker.co.uk",
        "phone": "07700900123",
        "name": "Tom Baker",
    }
    assert "Tom Baker" in a.summary


async def test_heuristic_only_returns_requested_fields() -> None:
    a = await HeuristicAnalyser().analyse(
        _call("my name is Ann Lee, ann@x.io"), [RequiredField(name="email")]
    )
    assert a.extracted == {"email": "ann@x.io"}


async def test_openai_analyser_parses_json_and_falls_back() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            body = {
                "choices": [
                    {
                        "message": {
                            "content": '{"summary": "Caller booked a boiler service.",'
                            ' "extracted": {"name": "Ann Lee", "postcode": null}}'
                        }
                    }
                ]
            }
            return httpx.Response(200, json=body)
        return httpx.Response(500)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://llm.test/v1"
    )
    analyser = OpenAIAnalyser("k", client=client)
    fields = [RequiredField(name="name"), RequiredField(name="postcode")]
    ok = await analyser.analyse(_call("my name is Ann Lee"), fields)
    assert ok.summary == "Caller booked a boiler service."
    assert ok.extracted == {"name": "Ann Lee"}  # nulls dropped
    fallback = await analyser.analyse(_call("my name is Ann Lee"), fields)
    assert fallback.extracted == {"name": "Ann Lee"}
    assert "caller turn" in fallback.summary


async def test_process_call_flags_missing_and_classifies_returning() -> None:
    store = MemoryStore(None)
    await store.set_required_fields(
        "a", [RequiredField(name="email"), RequiredField(name="ref", required=False)]
    )

    async def ingest(call_id: str) -> None:
        for t, p in [
            (CallEventType.CALL_STARTED, {"caller": "+447700900001"}),
            (
                CallEventType.CALL_ENDED,
                {"transcript": [{"role": "user", "text": "no email from me"}]},
            ),
        ]:
            await store.apply_event(
                CallEvent(
                    type=t,
                    call_id=call_id,
                    tenant_id="t",
                    company_id="co",
                    assistant_id="a",
                    payload=p,
                )
            )

    await ingest("c1")
    r1 = await process_call(store, HeuristicAnalyser(), "c1")
    assert r1 is not None
    assert r1.missed_fields == ["email"]
    assert r1.caller_type == "new"

    await ingest("c2")
    r2 = await process_call(store, HeuristicAnalyser(), "c2")
    assert r2 is not None
    assert r2.caller_type == "returning"
    assert r2.contact_id == r1.contact_id
    stored = await store.get_call("c2")
    assert stored is not None and stored.summary == r2.summary

    assert await process_call(store, HeuristicAnalyser(), "missing") is None
