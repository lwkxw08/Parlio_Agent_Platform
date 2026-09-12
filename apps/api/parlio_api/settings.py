from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PARLIO_", extra="ignore")

    env: str = "dev"
    worker_api_key: str = "dev-worker-key"
    redis_url: str | None = "redis://localhost:6379/0"
    events_stream: str = "parlio:call_events"
    events_consumer_group: str = "core-api"
    seed_demo_assistant: bool = True
    demo_number: str = "+440000000000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
