"""Voice catalogue + preview (Assistant Studio voice picker)."""

from __future__ import annotations

import base64

import httpx
import pytest
from httpx import AsyncClient

from parlio_api.voices import (
    CATALOGUE,
    MARKETS,
    PreviewRequest,
    PreviewUnavailable,
    VoicePlatformSettings,
    VoicePreviewer,
    default_voice,
    find_voice,
)
from parlio_voice.models import TTSProvider, VoiceConfig

OWNER = {"X-Parlio-User": "owner@demo.parlio.local"}


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
    assert body["provider"] == "cartesia" and body["market"] == "GB"
    assert body["accents"] == ["British", "Irish"]
    assert {v["provider"] for v in body["voices"]} == {"cartesia"}
    assert {v["gender"] for v in body["voices"]} == {"female", "male"}
    assert body["preview_available"] is False
    r = await client.post("/v1/voices/preview", json={"provider": "cartesia", "voice_id": "abc"})
    assert r.status_code == 503


def test_markets_have_recommended_voices_in_leading_accent() -> None:
    for code, m in MARKETS.items():
        v = default_voice(TTSProvider.CARTESIA, code)
        assert v is not None and v.accent == m.accents[0] and v.recommended, code
    assert default_voice(TTSProvider.CARTESIA, "ZZ") == default_voice(TTSProvider.CARTESIA, "GB")


def test_platform_settings_provider_per_market() -> None:
    s = VoicePlatformSettings(provider_by_market={"US": TTSProvider.ELEVENLABS})
    assert s.provider_for("gb") == TTSProvider.CARTESIA
    assert s.provider_for("us") == TTSProvider.ELEVENLABS


@pytest.mark.asyncio
async def test_platform_owner_sets_provider_and_market(client: AsyncClient) -> None:
    # tenants do not get to pick the provider: a saved ElevenLabs config is pinned back
    a = (await client.get("/v1/assistants")).json()[0]
    a["voice"] = {"provider": "elevenlabs", "voice_id": "JBFqnCBsd6RMkjVDRZzb", "speed": None}
    r = await client.put(f"/v1/assistants/{a['assistant_id']}", json={"config": a, "numbers": []})
    assert r.status_code == 200
    assert r.json()["voice"]["provider"] == "cartesia"
    assert find_voice(TTSProvider.CARTESIA, r.json()["voice"]["voice_id"]).accent == "British"

    # platform owner moves the US market to ElevenLabs and puts the demo tenant in the US
    r = await client.put(
        "/v1/admin/voice",
        json={"default_provider": "cartesia", "provider_by_market": {"US": "elevenlabs"}},
        headers=OWNER,
    )
    assert r.status_code == 200, r.text
    r = await client.put(
        "/v1/admin/voice", json={"provider_by_market": {"XX": "elevenlabs"}}, headers=OWNER
    )
    assert r.status_code == 400
    r = await client.put(
        f"/v1/admin/tenants/{a['tenant_id']}/locale", json={"market": "us"}, headers=OWNER
    )
    assert r.status_code == 200 and r.json()["market"] == "US"

    body = (await client.get("/v1/voices")).json()
    assert body["provider"] == "elevenlabs" and body["market"] == "US"
    assert body["accents"] == ["American"]
    assert {v["provider"] for v in body["voices"]} == {"elevenlabs"}

    # the tenant's existing Cartesia voice is swapped for the market default on next save
    a = (await client.get("/v1/assistants")).json()[0]
    r = await client.put(f"/v1/assistants/{a['assistant_id']}", json={"config": a, "numbers": []})
    assert r.json()["voice"]["provider"] == "elevenlabs"
    detail = (await client.get(f"/v1/admin/tenants/{a['tenant_id']}", headers=OWNER)).json()
    assert detail["locale"]["market"] == "US"
