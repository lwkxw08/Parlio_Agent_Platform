"""Voice catalogue + preview for Assistant Studio.

A curated list of receptionist-suitable voices per TTS provider (ids from the providers'
public libraries), tagged by accent so the Studio can lead with voices that match the tenant's
market (UK today; US/AU/IE as we onboard internationally). Which TTS provider a tenant uses is
a platform decision (`VoicePlatformSettings`, editable in Platform admin), not a Studio choice.
Previews return base64 MP3 in JSON so the dashboard can reuse its normal authenticated JSON
transport.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from parlio_voice.models import TTSProvider

log = logging.getLogger(__name__)

Gender = Literal["female", "male"]


class Market(BaseModel):
    """A country we sell into: which accents to lead with in the voice catalogue."""

    code: str
    name: str
    accents: list[str]


MARKETS: dict[str, Market] = {
    m.code: m
    for m in [
        Market(code="GB", name="United Kingdom", accents=["British", "Irish"]),
        Market(code="IE", name="Ireland", accents=["Irish", "British"]),
        Market(code="US", name="United States", accents=["American"]),
        Market(code="CA", name="Canada", accents=["American"]),
        Market(code="AU", name="Australia", accents=["Australian"]),
        Market(code="NZ", name="New Zealand", accents=["Australian", "British"]),
    ]
}
DEFAULT_MARKET = "GB"


class VoicePlatformSettings(BaseModel):
    """Platform-owner choice of TTS provider: a default plus optional per-market overrides."""

    default_provider: TTSProvider = TTSProvider.CARTESIA
    provider_by_market: dict[str, TTSProvider] = Field(default_factory=dict)
    default_market: str = DEFAULT_MARKET
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def provider_for(self, market: str) -> TTSProvider:
        return self.provider_by_market.get(market.upper(), self.default_provider)


class TenantLocale(BaseModel):
    """Where a tenant's business is - drives voice accents now, STT/number formats later."""

    tenant_id: str
    market: str = DEFAULT_MARKET
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


CARTESIA_DEFAULT_VOICE = "c46cf1f6-49a1-4d67-9a57-ff859a4046d3"  # Cora - Service Specialist
ELEVENLABS_DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # George


class Voice(BaseModel):
    provider: TTSProvider
    id: str
    name: str
    gender: Gender
    accent: str
    description: str
    recommended: bool = False


def _c(
    vid: str, name: str, gender: Gender, desc: str, accent: str = "British", rec: bool = False
) -> Voice:
    return Voice(
        provider=TTSProvider.CARTESIA,
        id=vid,
        name=name,
        gender=gender,
        accent=accent,
        description=desc,
        recommended=rec,
    )


CATALOGUE: list[Voice] = [
    # -- Cartesia (Sonic): female -----------------------------------------------------------
    _c(
        CARTESIA_DEFAULT_VOICE,
        "Cora",
        "female",
        "Helpful, articulate tone - ideal for customer support and reception.",
        rec=True,
    ),
    _c(
        "fb02b554-7d64-4f90-841e-e57fc88f410c",
        "Ailsa",
        "female",
        "Calm, personable and warm - friendly guidance and relaxed service calls.",
        rec=True,
    ),
    _c(
        "273f9ef7-9fc2-4def-88bb-ab108c6249ca",
        "Julia",
        "female",
        "Soft, graceful, composed rhythm - reassurance and polished guidance.",
    ),
    _c(
        "dc30854e-e398-4579-9dc8-16f6cb2c19b9",
        "Victoria",
        "female",
        "Crisp and professional - clear, reassuring communication.",
    ),
    _c(
        "62ae83ad-4f6a-430b-af41-a9bede9286ca",
        "Gemma",
        "female",
        "Confident and emotive - decisive professional assistance.",
    ),
    _c(
        "2f251ac3-89a9-4a77-a452-704b474ccd01",
        "Lucy",
        "female",
        "Reassuring and capable - everyday customer assistance.",
    ),
    _c(
        "81cd8d19-45e7-47b2-ad0e-bcd94f557ad0",
        "Pippa",
        "female",
        "Bright and upbeat - friendly onboarding and lively support.",
    ),
    _c(
        "16a4052e-1f11-47ac-95f5-9330bee062f9",
        "Courtney",
        "female",
        "Warm, measured delivery - good for complex or sensitive information.",
    ),
    _c(
        "e5d4c33a-d8f6-46e8-a10f-b5afecc35648",
        "Evie",
        "female",
        "Formal and polished - corporate and professional-services tone.",
    ),
    _c(
        "1e9b9b3d-d2ce-4cac-9d05-bc36a63fa28e",
        "Saira",
        "female",
        "Warm and attentive - organised, thoughtful follow-up.",
    ),
    _c(
        "d79d2b77-9192-4e10-9407-5d43ca034803",
        "Siobhan",
        "female",
        "Approachable and friendly everyday dialogue.",
        accent="Irish",
        rec=True,
    ),
    _c(
        "f786b574-daa5-4673-aa0c-cbe3e8534c02",
        "Katie",
        "female",
        "Clear, enunciating young adult voice - conversational support.",
        accent="American",
        rec=True,
    ),
    _c(
        "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4",
        "Skylar",
        "female",
        "Approachable and friendly - customer care and support.",
        accent="American",
        rec=True,
    ),
    _c(
        "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        "Jacqueline",
        "female",
        "Confident and reassuring - empathetic customer support.",
        accent="American",
    ),
    _c(
        "25d7abcb-4d6d-4aca-adce-8a1c85620c8b",
        "Jessica",
        "female",
        "Crisp and articulate - clear information sharing.",
        accent="American",
    ),
    _c(
        "d7bf7d75-64b7-4c1e-86c0-79d647366587",
        "Michelle",
        "female",
        "Gentle and reassuring - comfort and trust (care, health).",
        accent="American",
    ),
    _c(
        "391f4c0a-f1a8-4c21-9aa2-7a07f0a4b0dc",
        "Bronte",
        "female",
        "Bright and trustworthy - approachable everyday guidance.",
        accent="Australian",
        rec=True,
    ),
    # -- Cartesia (Sonic): male -------------------------------------------------------------
    _c(
        "3d5ce2fb-e56c-42f0-9ed9-4662484063b4",
        "Toby",
        "male",
        "Warm, conversational with a polished tone - all-round receptionist.",
        rec=True,
    ),
    _c(
        "ee7ea9f8-c0c1-498c-9279-764d6b56d189",
        "Oliver",
        "male",
        "Polite young adult voice - customer-facing and approachable.",
        rec=True,
    ),
    _c(
        "ef191366-f52f-447a-a398-ed8c0f2943a1",
        "Archie",
        "male",
        "Warm and casual - engaging, down-to-earth dialogue.",
    ),
    _c(
        "3faa81ae-d3d8-4ab1-9e44-e50e46d33c30",
        "Jasper",
        "male",
        "Warm and expressive - support and sales conversations.",
    ),
    _c(
        "df89f42f-f285-4613-adbf-14eedcec4c9e",
        "Harrison",
        "male",
        "Crisp and professional - efficient customer interactions.",
    ),
    _c(
        "4bc3cb8c-adb9-4bb8-b5d5-cbbef950b991",
        "George",
        "male",
        "Steady and capable - calm, dependable assistance.",
    ),
    _c(
        "c8f7835e-28a3-4f0c-80d7-c1302ac62aae",
        "Alistair",
        "male",
        "Sophisticated and steady - premium or professional services.",
    ),
    _c(
        "5e7d492a-5502-482e-b315-ebf587427806",
        "Alfie",
        "male",
        "Calm, balanced delivery - thoughtful guidance.",
    ),
    _c(
        "0ad65e7f-006c-47cf-bd31-52279d487913",
        "Rupert",
        "male",
        "Warm, mature voice - caring, reassuring conversations (care, health).",
    ),
    _c(
        "17044048-bfab-44b2-9532-9c1b65e9c217",
        "Alec",
        "male",
        "Lively and upbeat - welcoming energy for sales and enquiries.",
    ),
    _c(
        "dcddf1f4-b114-4b5d-9158-895cbba0e406",
        "Martin",
        "male",
        "Mature, composed and precise - trades and practical guidance.",
    ),
    _c(
        "d3e3d5d5-07b0-484f-9967-dbc8f15b60d5",
        "Ronan",
        "male",
        "Trustworthy and calm - financial and professional guidance.",
        accent="Irish",
    ),
    _c(
        "a5136bf9-224c-4d76-b823-52bd5efcffcc",
        "Jameson",
        "male",
        "Friendly and laid-back - customer support and onboarding.",
        accent="American",
        rec=True,
    ),
    _c(
        "86e30c1d-714b-4074-a1f2-1cb6b552fb49",
        "Carson",
        "male",
        "Friendly young adult voice - support conversations.",
        accent="American",
    ),
    _c(
        "1fcd23d0-bf12-4896-8f60-4f21ef5c9b98",
        "Austin",
        "male",
        "Reliable and approachable - dependable everyday assistance.",
        accent="American",
    ),
    _c(
        "aa2cafe9-97ba-4052-ac3c-875000f95212",
        "Zander",
        "male",
        "Measured and calm - professional guidance and reassurance.",
        accent="American",
    ),
    _c(
        "7d444628-dd13-442b-b687-71a6baf0c07e",
        "Joseph",
        "male",
        "Gentle and reassuring - comfort and trust (care, health).",
        accent="American",
    ),
    _c(
        "12e85709-099c-480a-ba3e-875c41a9611a",
        "Arlo",
        "male",
        "Friendly, mid-toned voice - clear customer support.",
        accent="Australian",
        rec=True,
    ),
    _c(
        "79d2cf27-444a-4c3a-9eed-2ad5cf795a3b",
        "Fraser",
        "male",
        "Bright and articulate - clear, confident delivery.",
        accent="Australian",
    ),
    # -- ElevenLabs (Flash): a few library voices -------------------------------------------
    Voice(
        provider=TTSProvider.ELEVENLABS,
        id=ELEVENLABS_DEFAULT_VOICE,
        name="George",
        gender="male",
        accent="British",
        description="Warm, mature storyteller - calm and reassuring.",
        recommended=True,
    ),
    Voice(
        provider=TTSProvider.ELEVENLABS,
        id="Xb7hH8MSUJpSbSDYk0k2",
        name="Alice",
        gender="female",
        accent="British",
        description="Clear, confident and professional.",
        recommended=True,
    ),
    Voice(
        provider=TTSProvider.ELEVENLABS,
        id="pFZP5JQG7iQjIQuC4Bku",
        name="Lily",
        gender="female",
        accent="British",
        description="Warm, velvety and calm.",
    ),
    Voice(
        provider=TTSProvider.ELEVENLABS,
        id="onwK4e9ZLuTAKqWW03F9",
        name="Daniel",
        gender="male",
        accent="British",
        description="Authoritative, broadcast-style delivery.",
    ),
]


def find_voice(provider: TTSProvider, voice_id: str) -> Voice | None:
    return next((v for v in CATALOGUE if v.provider == provider and v.id == voice_id), None)


def default_voice(provider: TTSProvider, market: str) -> Voice | None:
    """Recommended voice in the market's leading accent, else any recommended, else first."""
    accents = MARKETS.get(market.upper(), MARKETS[DEFAULT_MARKET]).accents
    pool = [v for v in CATALOGUE if v.provider == provider]
    for acc in accents:
        hit = next((v for v in pool if v.accent == acc and v.recommended), None)
        if hit:
            return hit
    return next((v for v in pool if v.recommended), pool[0] if pool else None)


class PreviewRequest(BaseModel):
    provider: TTSProvider = TTSProvider.CARTESIA
    voice_id: str = Field(min_length=3, max_length=120)
    text: str = Field(default="", max_length=300)
    speed: float | None = None
    language: str = "en"


class Preview(BaseModel):
    provider: TTSProvider
    voice_id: str
    mime: str
    audio_b64: str


DEFAULT_SAMPLE = (
    "Good morning, thanks for calling {business}. You're through to the assistant - "
    "how can I help you today?"
)


class PreviewUnavailable(Exception):
    """Provider not configured or refused the request; message is safe to show users."""


class VoicePreviewer:
    def __init__(
        self,
        cartesia_key: str | None,
        elevenlabs_key: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cartesia = cartesia_key
        self._eleven = elevenlabs_key
        self._client = client

    def available(self, provider: TTSProvider) -> bool:
        return bool(self._cartesia if provider == TTSProvider.CARTESIA else self._eleven)

    async def preview(self, req: PreviewRequest) -> Preview:
        if not self.available(req.provider):
            raise PreviewUnavailable(
                f"Voice previews need a {req.provider.value} API key on the server."
            )
        text = req.text.strip() or DEFAULT_SAMPLE.format(business="Parlio")
        client = self._client or httpx.AsyncClient(timeout=20)
        try:
            if req.provider == TTSProvider.CARTESIA:
                audio = await self._cartesia_tts(client, req, text)
            else:
                audio = await self._elevenlabs_tts(client, req, text)
        except httpx.HTTPError as e:
            log.warning("voice preview failed provider=%s: %s", req.provider.value, e)
            raise PreviewUnavailable("The voice provider didn't return audio - try again.") from e
        finally:
            if self._client is None:
                await client.aclose()
        return Preview(
            provider=req.provider,
            voice_id=req.voice_id,
            mime="audio/mpeg",
            audio_b64=base64.b64encode(audio).decode(),
        )

    async def _cartesia_tts(
        self, client: httpx.AsyncClient, req: PreviewRequest, text: str
    ) -> bytes:
        body: dict[str, object] = {
            "model_id": "sonic-3",
            "transcript": text,
            "voice": {"mode": "id", "id": req.voice_id},
            "language": req.language,
            "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000},
        }
        if req.speed is not None:
            body["speed"] = req.speed
        r = await client.post(
            "https://api.cartesia.ai/tts/bytes",
            json=body,
            headers={"X-API-Key": self._cartesia or "", "Cartesia-Version": "2025-04-16"},
        )
        r.raise_for_status()
        return r.content

    async def _elevenlabs_tts(
        self, client: httpx.AsyncClient, req: PreviewRequest, text: str
    ) -> bytes:
        body: dict[str, object] = {"text": text, "model_id": "eleven_flash_v2_5"}
        if req.speed is not None:
            body["voice_settings"] = {"speed": req.speed}
        r = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{req.voice_id}",
            params={"output_format": "mp3_44100_128"},
            json=body,
            headers={"xi-api-key": self._eleven or ""},
        )
        r.raise_for_status()
        return r.content
