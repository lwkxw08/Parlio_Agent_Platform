from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PARLIO_", extra="ignore")

    env: str = "dev"
    region: str = "uk"

    api_url: str = "http://localhost:8000"
    worker_api_key: str = "dev-worker-key"
    redis_url: str = "redis://localhost:6379/0"
    events_stream: str = "parlio:call_events"
    config_cache_ttl_s: int = 60

    demo_mode: bool = Field(
        default=True,
        description="Answer with a built-in demo assistant when the Core API has no config.",
    )

    outbound_sip_trunk_id: str | None = Field(
        default=None,
        description="LiveKit outbound SIP trunk used to dial humans for warm transfers.",
    )

    recording_bucket: str | None = None
    recording_s3_endpoint: str | None = None
    recording_s3_region: str = "auto"
    recording_s3_access_key: str | None = None
    recording_s3_secret_key: str | None = None

    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    groq_api_key: str | None = None
    deepgram_eu_base_url: str = "https://api.eu.deepgram.com/v1/listen"

    @property
    def recording_configured(self) -> bool:
        return bool(
            self.recording_bucket and self.recording_s3_access_key and self.recording_s3_secret_key
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
