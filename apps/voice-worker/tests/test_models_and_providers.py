import pytest

from parlio_voice.models import (
    AssistantConfig,
    LLMProvider,
    ProviderChain,
    RegionProfile,
    STTProvider,
    TTSProvider,
)
from parlio_voice.providers import _ALLOWED, _filter


def _cfg(**kw) -> AssistantConfig:  # type: ignore[no-untyped-def]
    base = {"tenant_id": "t", "company_id": "c", "assistant_id": "a", "business_name": "Acme"}
    return AssistantConfig(**{**base, **kw})


def test_greeting_and_instructions_render_business_name() -> None:
    cfg = _cfg(name="Ada")
    assert "Acme" in cfg.rendered_greeting()
    assert cfg.rendered_instructions().startswith("You are Ada")


def test_consent_falls_back_to_english() -> None:
    cfg = _cfg(language="fr")
    assert cfg.consent_text() == cfg.recording.consent_announcement["en"]
    cfg.recording.enabled = False
    assert cfg.consent_text() is None


def test_sovereign_profile_filters_us_providers() -> None:
    chain = [STTProvider.DEEPGRAM, STTProvider.DEEPGRAM_EU]
    allowed, _, _ = _ALLOWED[RegionProfile.SOVEREIGN_UK]
    assert _filter(chain, allowed, "stt", RegionProfile.SOVEREIGN_UK) == [STTProvider.DEEPGRAM_EU]


def test_sovereign_profile_rejects_chain_with_no_permitted_provider() -> None:
    _, allowed_llm, _ = _ALLOWED[RegionProfile.SOVEREIGN_UK]
    with pytest.raises(ValueError, match="no llm provider"):
        _filter(
            [LLMProvider.OPENAI, LLMProvider.ANTHROPIC],
            allowed_llm,
            "llm",
            RegionProfile.SOVEREIGN_UK,
        )


def test_standard_profile_keeps_full_chain() -> None:
    chain = ProviderChain(
        llm=[LLMProvider.OPENAI, LLMProvider.ANTHROPIC],
        tts=[TTSProvider.CARTESIA, TTSProvider.ELEVENLABS],
    )
    _, allowed_llm, allowed_tts = _ALLOWED[RegionProfile.STANDARD]
    assert _filter(chain.llm, allowed_llm, "llm", RegionProfile.STANDARD) == chain.llm
    assert _filter(chain.tts, allowed_tts, "tts", RegionProfile.STANDARD) == chain.tts


def test_config_round_trips_json() -> None:
    cfg = _cfg(region_profile=RegionProfile.SOVEREIGN_UK)
    assert AssistantConfig.model_validate_json(cfg.model_dump_json()) == cfg
