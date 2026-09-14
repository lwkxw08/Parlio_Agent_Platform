"""Voice catalogue + preview for Assistant Studio.

A curated list of UK/Irish receptionist-suitable voices per TTS provider (ids from the
providers' public libraries) and a short preview synthesised via the provider's REST API so
users can hear a voice before saving. Previews return base64 MP3 in JSON so the dashboard can
reuse its normal authenticated JSON transport.
"""

from __future__ import annotations

import base64
import logging
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from parlio_voice.models import TTSProvider

log = logging.getLogger(__name__)

Gender = Literal["female", "male"]

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
    ),
    _c(
        "f786b574-daa5-4673-aa0c-cbe3e8534c02",
        "Katie",
        "female",
        "Clear, enunciating young adult voice (previous Parlio default).",
        accent="American",
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
