"""Tenant/assistant configuration and call-event models shared with the Core API."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class RegionProfile(StrEnum):
    STANDARD = "standard"
    SOVEREIGN_UK = "sovereign-uk"
    SOVEREIGN_UK_STRICT = "sovereign-uk-strict"


class STTProvider(StrEnum):
    DEEPGRAM = "deepgram"
    DEEPGRAM_EU = "deepgram-eu"


class LLMProvider(StrEnum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure-openai"
    ANTHROPIC = "anthropic"
    GROQ = "groq"


class TTSProvider(StrEnum):
    CARTESIA = "cartesia"
    ELEVENLABS = "elevenlabs"


class VoiceConfig(BaseModel):
    provider: TTSProvider = TTSProvider.CARTESIA
    voice_id: str = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
    speed: float | None = None


class ProviderChain(BaseModel):
    """Primary provider plus ordered fallbacks; resolved per region profile."""

    stt: list[STTProvider] = Field(default_factory=lambda: [STTProvider.DEEPGRAM])
    llm: list[LLMProvider] = Field(default_factory=lambda: [LLMProvider.OPENAI])
    tts: list[TTSProvider] = Field(default_factory=lambda: [TTSProvider.CARTESIA])


class TurnTuning(BaseModel):
    min_endpointing_delay: float = 0.2
    max_endpointing_delay: float = 2.0
    allow_interruptions: bool = True
    min_interruption_duration: float = 0.4
    preemptive_generation: bool = True


class RecordingConfig(BaseModel):
    enabled: bool = True
    consent_announcement: dict[str, str] = Field(
        default_factory=lambda: {
            "en": "This call may be recorded for quality and training purposes.",
        }
    )


class AssistantConfig(BaseModel):
    tenant_id: str
    company_id: str
    assistant_id: str
    assistant_version: int = 1
    name: str = "Parlio"
    business_name: str = "the business"
    language: str = "en"
    greeting: str = "Hi, thanks for calling {business_name}. How can I help you today?"
    instructions: str = (
        "You are {name}, the friendly and efficient phone receptionist for {business_name}. "
        "Keep answers short (one or two sentences), speak naturally, never invent facts. "
        "If you do not know something, offer to take a message."
    )
    region_profile: RegionProfile = RegionProfile.STANDARD
    providers: ProviderChain = Field(default_factory=ProviderChain)
    llm_model: str | None = None
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    turn: TurnTuning = Field(default_factory=TurnTuning)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)

    def rendered_greeting(self) -> str:
        return self.greeting.format(name=self.name, business_name=self.business_name)

    def rendered_instructions(self) -> str:
        return self.instructions.format(name=self.name, business_name=self.business_name)

    def consent_text(self) -> str | None:
        if not self.recording.enabled:
            return None
        ann = self.recording.consent_announcement
        return ann.get(self.language) or ann.get("en")


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallEventType(StrEnum):
    CALL_STARTED = "call.started"
    CALL_ANSWERED = "call.answered"
    TURN_COMPLETED = "call.turn_completed"
    TRANSCRIPT_ITEM = "call.transcript_item"
    RECORDING_STARTED = "call.recording_started"
    CALL_ENDED = "call.ended"
    CALL_FAILED = "call.failed"


class CallEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    type: CallEventType
    call_id: str
    tenant_id: str
    company_id: str
    assistant_id: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)


class TurnLatency(BaseModel):
    """Latency budget for one agent turn (all seconds)."""

    speech_id: str
    end_of_utterance_delay: float | None = None
    transcription_delay: float | None = None
    llm_ttft: float | None = None
    tts_ttfb: float | None = None

    @property
    def total(self) -> float | None:
        parts = [self.end_of_utterance_delay, self.llm_ttft, self.tts_ttfb]
        if any(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)
