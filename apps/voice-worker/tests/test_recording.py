"""Recording must never hold up a call: egress failures and hangs degrade to 'no recording'."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from parlio_voice import recording
from parlio_voice.recording import CallRecorder
from parlio_voice.settings import Settings


def _settings() -> Settings:
    return Settings(
        recording_s3_endpoint="http://127.0.0.1:9000",
        recording_bucket="parlio-recordings",
        recording_s3_access_key="k",
        recording_s3_secret_key="s",
    )


class _Egress:
    def __init__(self, behaviour: str) -> None:
        self.behaviour = behaviour

    async def start_track_egress(self, req: object) -> SimpleNamespace:
        if self.behaviour == "hang":
            await asyncio.sleep(60)
        if self.behaviour == "error":
            raise RuntimeError("twirp error unknown: no response from servers, status=503")
        return SimpleNamespace(egress_id="EG_1")


def _recorder(behaviour: str) -> CallRecorder:
    lk = SimpleNamespace(egress=_Egress(behaviour))
    room = SimpleNamespace(name="RM_1")
    return CallRecorder(_settings(), lk, room)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_egress_error_is_swallowed() -> None:
    rec = _recorder("error")
    assert await rec.record_track("t", "c", "caller", "TR_1") is None
    assert rec.object_keys == [] and rec.egress_ids == []


@pytest.mark.asyncio
async def test_egress_hang_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recording, "EGRESS_START_TIMEOUT_S", 0.05)
    rec = _recorder("hang")
    assert await asyncio.wait_for(rec.record_track("t", "c", "caller", "TR_1"), 2) is None
    assert rec.object_keys == []


@pytest.mark.asyncio
async def test_egress_ok_records_key() -> None:
    rec = _recorder("ok")
    key = await rec.record_track("acme", "c1", "agent", "TR_2")
    assert key is not None and key.startswith("recordings/acme/") and key.endswith("/c1/agent.ogg")
    assert rec.egress_ids == ["EG_1"]
