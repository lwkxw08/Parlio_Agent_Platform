from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PARLIO_", extra="ignore")

    env: str = "dev"
    # Bootstrap key from the environment; per-tenant keys live in `worker_api_keys` (hashed).
    worker_api_key: str = "dev-worker-key"
    redis_url: str | None = "redis://localhost:6379/0"
    events_stream: str = "parlio:call_events"
    events_consumer_group: str = "core-api"
    seed_demo_assistant: bool = True
    demo_number: str = "+440000000000"

    store_backend: Literal["memory", "postgres"] = "memory"
    database_url: str = "postgresql+asyncpg://parlio:parlio@localhost:5432/parlio"
    db_auto_migrate: bool = True  # dev/staging convenience; prod runs `alembic upgrade` in CD
    db_pool_size: int = 10

    postcall_analyser: Literal["heuristic", "openai"] = "heuristic"
    postcall_concurrency: int = 4
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    return Settings()
