"""Build STT/LLM/TTS/VAD instances for an assistant, honouring its region profile.

Each layer returns a FallbackAdapter over the configured provider chain so a vendor
outage degrades to the next provider instead of dropping the call.
"""

from __future__ import annotations

import logging

from livekit.agents import llm, stt, tts, vad
from livekit.plugins import anthropic, cartesia, deepgram, elevenlabs, openai, silero

from parlio_voice.models import (
    AssistantConfig,
    LLMProvider,
    RegionProfile,
    STTProvider,
    TTSProvider,
)
from parlio_voice.settings import Settings

log = logging.getLogger("parlio.providers")

_STT_LANG = {"en": "en-GB", "es": "es", "fr": "fr", "de": "de", "pt": "pt", "it": "it"}

# Neutral British voices used when a vendor is only a fallback (voice ids are vendor-specific).
_CARTESIA_FALLBACK_VOICE = "c46cf1f6-49a1-4d67-9a57-ff859a4046d3"  # Cora (British)
_ELEVENLABS_FALLBACK_VOICE = "JBFqnCBsd6RMkjVDRZzb"

_DEFAULT_LLM_MODEL = {
    LLMProvider.OPENAI: "gpt-4o-mini",
    LLMProvider.AZURE_OPENAI: "gpt-4o-mini",
    LLMProvider.ANTHROPIC: "claude-haiku-4-5",
    LLMProvider.GROQ: "llama-3.3-70b-versatile",
}

# Providers permitted per region profile. Sovereign tiers are the same vendors pinned to
# UK/EU endpoints; strict mode is self-hosted only and not wired yet (Phase 16).
_ALLOWED: dict[RegionProfile, tuple[set[STTProvider], set[LLMProvider], set[TTSProvider]]] = {
    RegionProfile.STANDARD: (set(STTProvider), set(LLMProvider), set(TTSProvider)),
    RegionProfile.SOVEREIGN_UK: (
        {STTProvider.DEEPGRAM_EU},
        {LLMProvider.AZURE_OPENAI},
        {TTSProvider.ELEVENLABS},
    ),
    RegionProfile.SOVEREIGN_UK_STRICT: (set(), set(), set()),
}


def load_vad() -> vad.VAD:
    return silero.VAD.load(min_silence_duration=0.35, prefix_padding_duration=0.3)


def _filter[T](chain: list[T], allowed: set[T], layer: str, profile: RegionProfile) -> list[T]:
    out = [p for p in chain if p in allowed]
    if not out:
        raise ValueError(f"no {layer} provider permitted for region profile {profile}")
    dropped = [p for p in chain if p not in allowed]
    if dropped:
        log.info("dropped %s providers %s for profile %s", layer, dropped, profile)
    return out


def build_stt(cfg: AssistantConfig, settings: Settings) -> stt.STT:
    allowed_stt, _, _ = _ALLOWED[cfg.region_profile]
    chain = _filter(cfg.providers.stt, allowed_stt, "stt", cfg.region_profile)
    lang = _STT_LANG.get(cfg.language, cfg.language)
    instances: list[stt.STT] = []
    for p in chain:
        match p:
            case STTProvider.DEEPGRAM:
                instances.append(
                    deepgram.STT(model="nova-3", language=lang, endpointing_ms=25, no_delay=True)
                )
            case STTProvider.DEEPGRAM_EU:
                instances.append(
                    deepgram.STT(
                        model="nova-3",
                        language=lang,
                        endpointing_ms=25,
                        no_delay=True,
                        base_url=settings.deepgram_eu_base_url,
                    )
                )
    return instances[0] if len(instances) == 1 else stt.FallbackAdapter(instances)


def build_llm(cfg: AssistantConfig, settings: Settings) -> llm.LLM:
    _, allowed_llm, _ = _ALLOWED[cfg.region_profile]
    chain = _filter(cfg.providers.llm, allowed_llm, "llm", cfg.region_profile)
    instances: list[llm.LLM] = []
    for p in chain:
        model = cfg.llm_model or _DEFAULT_LLM_MODEL[p]
        match p:
            case LLMProvider.OPENAI:
                instances.append(openai.LLM(model=model, temperature=0.4))
            case LLMProvider.AZURE_OPENAI:
                instances.append(
                    openai.LLM.with_azure(
                        model=model,
                        azure_endpoint=settings.azure_openai_endpoint,
                        azure_deployment=settings.azure_openai_deployment or model,
                        api_version="2024-10-21",
                        temperature=0.4,
                    )
                )
            case LLMProvider.ANTHROPIC:
                instances.append(anthropic.LLM(model=model, temperature=0.4))
            case LLMProvider.GROQ:
                instances.append(
                    openai.LLM(
                        model=model,
                        base_url="https://api.groq.com/openai/v1",
                        api_key=settings.groq_api_key or "",
                        temperature=0.4,
                    )
                )
    return instances[0] if len(instances) == 1 else llm.FallbackAdapter(instances)


def build_tts(cfg: AssistantConfig, settings: Settings) -> tts.TTS:
    _, _, allowed_tts = _ALLOWED[cfg.region_profile]
    chain = list(dict.fromkeys([cfg.voice.provider, *cfg.providers.tts]))
    chain = _filter(chain, allowed_tts, "tts", cfg.region_profile)
    instances: list[tts.TTS] = []
    for p in chain:
        primary = p == cfg.voice.provider
        match p:
            case TTSProvider.CARTESIA:
                instances.append(
                    cartesia.TTS(
                        model="sonic-3",
                        language=cfg.language,
                        voice=cfg.voice.voice_id if primary else _CARTESIA_FALLBACK_VOICE,
                        speed=cfg.voice.speed if primary else None,
                    )
                )
            case TTSProvider.ELEVENLABS:
                instances.append(
                    elevenlabs.TTS(
                        model="eleven_flash_v2_5",
                        language=cfg.language,
                        streaming_latency=3,
                        voice_id=cfg.voice.voice_id if primary else _ELEVENLABS_FALLBACK_VOICE,
                    )
                )
    return instances[0] if len(instances) == 1 else tts.FallbackAdapter(instances)
