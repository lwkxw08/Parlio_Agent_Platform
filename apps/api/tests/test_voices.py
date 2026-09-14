"""Voice catalogue + preview (Assistant Studio voice picker)."""

from __future__ import annotations

import base64

import httpx
import pytest
from httpx import AsyncClient

from parlio_api.voices import (
    CATALOGUE,
    PreviewRequest,
    PreviewUnavailable,
    VoicePreviewer,
    find_voice,
)
from parlio_voice.models import TTSProvider, VoiceConfig


def test_catalogue_has_male_and_female_british_voices() -> None:
    cartesia = [v for v in CATALOGUE if v.provider == TTSProvider.CARTESIA]
    assert {v.gender for v in cartesia} == {"female", "male"}
    assert sum(v.accent == "British" for v in cartesia) >= 10
    assert any(v.accent == "Irish" for v in cartesia)
    assert len({(v.provider, v.id) for v in CATALOGUE}) == len(CATALOGUE)
    assert find_voice(TTSProvider.CARTESIA, VoiceConfig().voice_id) is not None
    assert any(v.recommended for v in CATALOGUE if v.provider == TTSProvider.ELEVENLABS)


@pytest.mark.asyncio
async def test_preview_requires_key() -> None:
    p = VoicePreviewer(None, None)
    assert not p.available(TTSProvider.CARTESIA)
    with pytest.raises(PreviewUnavailable):
        await p.preview(PreviewRequest(voice_id="abc"))


@pytest.mark.asyncio
async def test_preview_synthesises_mp3() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("x-api-key")
        seen["body"] = req.content
        return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = VoicePreviewer("sk-test", None, client=client)
    out = await p.preview(PreviewRequest(voice_id="c46cf1f6-49a1-4d67-9a57-ff859a4046d3"))
    assert out.mime == "audio/mpeg"
    assert base64.b64decode(out.audio_b64) == b"ID3mp3"
    assert str(seen["url"]).startswith("https://api.cartesia.ai/tts/bytes")
    assert seen["auth"] == "sk-test"
    assert b"thanks for calling" in bytes(seen["body"])  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_preview_provider_error_is_safe() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key sk-secret")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = VoicePreviewer(None, "el-test", client=client)
    with pytest.raises(PreviewUnavailable) as e:
        await p.preview(
            PreviewRequest(provider=TTSProvider.ELEVENLABS, voice_id="JBFqnCBsd6RMkjVDRZzb")
        )
    assert "sk-secret" not in str(e.value)


@pytest.mark.asyncio
async def test_voice_routes(client: AsyncClient) -> None:
    r = await client.get("/v1/voices")
    assert r.status_code == 200
    body = r.json()
    assert {v["gender"] for v in body["voices"]} == {"female", "male"}
    assert set(body["preview_available"]) == {"cartesia", "elevenlabs"}
    r = await client.post("/v1/voices/preview", json={"provider": "cartesia", "voice_id": "abc"})
    assert r.status_code == 503
