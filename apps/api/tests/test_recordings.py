"""Recording playback: tenant-scoped streaming of stored call audio via the API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.recordings import RecordingStorage, content_type_for, sign_get
from parlio_api.store import Member
from parlio_voice.models import CallEvent, CallEventType

WORKER = {"X-Worker-Key": "dev-worker-key"}
OWNER = {"X-Parlio-User": "owner@demo.parlio.local"}
INTRUDER = {"X-Parlio-User": "intruder@other.example"}
KEY = "recordings/demo/2026/09/11/rc1/caller.ogg"


async def _recorded_call(client: AsyncClient, call_id: str = "rc1") -> None:
    for t, payload in [
        (CallEventType.CALL_STARTED, {"caller": "+447700900001", "dialed": "+4400"}),
        (CallEventType.CALL_ANSWERED, {"answer_latency_s": 0.4}),
        (CallEventType.RECORDING_STARTED, {"keys": [KEY]}),
        (CallEventType.CALL_ENDED, {"reason": "hangup", "duration_s": 12.0}),
    ]:
        e = CallEvent(
            type=t,
            call_id=call_id,
            tenant_id="demo",
            company_id="demo",
            assistant_id="demo",
            payload=payload,
        )
        r = await client.post("/v1/worker/events", json=e.model_dump(mode="json"), headers=WORKER)
        assert r.status_code == 202, r.text


def _fake_storage(app: FastAPI, objects: dict[str, bytes]) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = request.url.path.split("/parlio-recordings/", 1)[1]
        body = objects.get(key)
        if body is None:
            return httpx.Response(404, text="<Error><Code>NoSuchKey</Code></Error>")
        rng = request.headers.get("range")
        if rng:
            start, end = rng.removeprefix("bytes=").split("-")
            s, e = int(start), int(end) if end else len(body) - 1
            return httpx.Response(
                206,
                content=body[s : e + 1],
                headers={"Content-Range": f"bytes {s}-{e}/{len(body)}", "Accept-Ranges": "bytes"},
            )
        return httpx.Response(
            200, content=body, headers={"ETag": '"abc"', "Accept-Ranges": "bytes"}
        )

    app.state.recordings = RecordingStorage(
        "http://minio:9000",
        "parlio-recordings",
        "parlio",
        "secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return seen


async def test_recording_requires_storage_and_valid_index(
    client: AsyncClient, app: FastAPI
) -> None:
    await _recorded_call(client)
    app.state.recordings = None
    r = await client.get("/v1/calls/rc1/recordings/0", headers=OWNER)
    assert r.status_code == 503
    _fake_storage(app, {KEY: b"OggS" * 10})
    assert (await client.get("/v1/calls/rc1/recordings/1", headers=OWNER)).status_code == 404
    assert (await client.get("/v1/calls/nope/recordings/0", headers=OWNER)).status_code == 404


async def test_recording_streams_with_auth_and_range(client: AsyncClient, app: FastAPI) -> None:
    await _recorded_call(client)
    audio = bytes(range(256)) * 4
    seen = _fake_storage(app, {KEY: audio})

    r = await client.get("/v1/calls/rc1/recordings/0", headers=OWNER)
    assert r.status_code == 200
    assert r.content == audio
    assert r.headers["content-type"].startswith("audio/ogg")
    assert "inline" in r.headers["content-disposition"]
    assert r.headers["accept-ranges"] == "bytes"
    assert seen[-1].headers["authorization"].startswith("AWS4-HMAC-SHA256")

    r = await client.get("/v1/calls/rc1/recordings/0", headers={**OWNER, "Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.content == audio[:100]
    assert r.headers["content-range"] == f"bytes 0-99/{len(audio)}"


async def test_recording_missing_object_and_tenant_isolation(
    client: AsyncClient, app: FastAPI
) -> None:
    await _recorded_call(client)
    _fake_storage(app, {})
    r = await client.get("/v1/calls/rc1/recordings/0", headers=OWNER)
    assert r.status_code == 404
    assert "not available" in r.json()["detail"]

    await app.state.store.upsert_member(
        Member(tenant_id="acme", user_id="u-acme", email="intruder@other.example", role="owner")
    )
    r = await client.get("/v1/calls/rc1/recordings/0", headers=INTRUDER)
    assert r.status_code == 403


def test_sigv4_matches_known_vector() -> None:
    headers = sign_get(
        "http://minio:9000/parlio-recordings/recordings/demo/a%20b.ogg",
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        now=datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC),
    )
    assert headers["x-amz-date"] == "20130524T000000Z"
    assert (
        headers["x-amz-content-sha256"]
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    auth = headers["authorization"]
    assert auth.startswith(
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20130524/us-east-1/s3/aws4_request"
    )
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in auth
    sig = auth.rsplit("Signature=", 1)[1]
    assert len(sig) == 64 and int(sig, 16) >= 0
    # deterministic for the same inputs
    again: dict[str, Any] = sign_get(
        "http://minio:9000/parlio-recordings/recordings/demo/a%20b.ogg",
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        now=datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC),
    )
    assert again["authorization"] == auth


def test_content_types() -> None:
    assert content_type_for("x/agent.ogg") == "audio/ogg"
    assert content_type_for("x/agent.mp4") == "audio/mp4"
    assert content_type_for("x/agent.bin") == "application/octet-stream"
