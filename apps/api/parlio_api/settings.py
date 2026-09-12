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

    # Dashboard auth: "dev" trusts X-Parlio-User / seeded demo owner; "supabase" verifies JWTs.
    auth_mode: Literal["dev", "supabase"] = "dev"
    supabase_url: str | None = None
    supabase_jwt_secret: str | None = None
    # Onboarding enrichment (optional; heuristics run without them)
    google_places_api_key: str | None = None
    dashboard_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Ticket alerts: Slack-incoming-webhook-compatible URL; unset = log only.
    notify_webhook_url: str | None = None
    sla_check_interval_s: float = 30.0

    # Phase 5 integrations. Key derives the Fernet key that seals tenant credentials at rest.
    vault_key: str = "dev-only-change-me"
    public_api_url: str = "http://localhost:8000"
    sms_provider: Literal["log", "telnyx"] = "log"
    sms_from_number: str | None = None  # E.164 sender (Telnyx SMS-capable number)
    telnyx_api_key: str | None = None
    telnyx_messaging_profile_id: str | None = None
    resend_api_key: str | None = None
    email_from: str = "Parlio <alerts@parlio.local>"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None

    # Phase 5b BYO SIP. "simulated" keeps trunks fully testable without a SIP edge.
    sip_provisioner: Literal["simulated", "livekit"] = "simulated"
    sip_domain: str = "sip.parlio.local"
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
