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
    # Marketing site (apps/marketing): the "Hear it for yourself" demo runs browser voice against
    # this tenant, capped per calendar month; the phone number is for visitors who prefer to dial.
    site_demo_tenant_id: str = "demo"
    site_demo_phone: str | None = None
    site_demo_monthly_minutes: int = 300
    site_demo_starts_per_ip_per_hour: int = 5
    site_url: str = "https://parliotec.com"
    # Cloudflare Pages production + preview hosts of the marketing site
    site_preview_origin_regex: str | None = (
        r"^https://([a-z0-9-]+\.)?parliotec-marketing\.pages\.dev$"
    )

    store_backend: Literal["memory", "postgres"] = "memory"
    database_url: str = "postgresql+asyncpg://parlio:parlio@localhost:5432/parlio"
    db_auto_migrate: bool = True  # dev/staging convenience; prod runs `alembic upgrade` in CD
    db_pool_size: int = 10

    # "auto" = OpenAI whenever a key is configured, otherwise the offline heuristics.
    postcall_analyser: Literal["auto", "heuristic", "openai"] = "auto"
    postcall_concurrency: int = 4
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    @property
    def llm_key(self) -> str | None:
        """OpenAI key to use for LLM features, or None when heuristics should be used."""
        if self.postcall_analyser == "heuristic":
            return None
        return self.openai_api_key or None

    # Object storage the worker's egress writes recordings to (same env names as the worker).
    recording_bucket: str | None = None
    recording_s3_endpoint: str | None = None
    recording_s3_region: str = "auto"
    recording_s3_access_key: str | None = None
    recording_s3_secret_key: str | None = None

    # TTS provider keys for Studio voice previews (the worker has its own copies).
    cartesia_api_key: str | None = None
    elevenlabs_api_key: str | None = None

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
    email_from: str = "ParlioTec <alerts@parlio.local>"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None

    # Phase 7 connectors: failed pushes are re-driven on this cadence (back-off per job).
    connector_retry_interval_s: float = 60.0

    # Phase 6: billing, observability, compliance
    billing_provider: Literal["simulated", "stripe"] = "simulated"
    # Live Stripe account (sk_live_ / its webhook signing secret).
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # Stripe sandbox (sk_test_ / its webhook signing secret). When both pairs are set, Platform
    # admin -> Billing picks which one is active; the default is sandbox.
    stripe_test_secret_key: str | None = None
    stripe_test_webhook_secret: str | None = None
    # Phase 12: one-off payment links texted mid-call (Stripe Checkout) - same Stripe account;
    # a separate webhook endpoint secret if you register a second endpoint.
    payments_provider: Literal["simulated", "stripe"] = "simulated"
    stripe_payments_webhook_secret: str | None = None
    # Phase 11b: LiveKit agent name the browser-voice dispatcher targets (worker registers it).
    agent_name: str = "parlio-voice"
    number_provider: Literal["simulated", "telnyx"] = "simulated"
    telnyx_sip_uri: str | None = None
    telnyx_connection_id: str | None = None
    # LiveKit inbound trunk the carrier delivers platform numbers to; bought numbers are added
    # to its DID list so the dispatch rule picks them up (unset = trunk accepts what it has).
    inbound_trunk_id: str | None = None
    trial_days: int = 14
    otlp_endpoint: str | None = None
    metrics_token: str | None = None
    target_turn_latency_s: float = 1.5
    rate_limit_per_minute: int = 600
    retention_sweep_interval_s: float = 3600.0
    # Phase 16b: emails granted the platform "owner" staff role on sign-in (bootstraps /admin).
    # Dev mode defaults to the demo owner so the console is reachable locally.
    platform_owner_emails: list[str] = ["owner@demo.parlio.local"]
    # Where marketing-site contact/sales enquiries are emailed; empty = platform owners.
    site_contact_emails: list[str] = []

    # Phase 9 outbound: "simulated" records dials; "livekit" dispatches the worker to dial via
    # the platform SIP outbound trunk.
    outbound_dialer: Literal["simulated", "livekit"] = "simulated"
    outbound_trunk_id: str | None = None  # LiveKit SIP outbound trunk (platform Telnyx)
    outbound_caller_id: str | None = None  # default E.164 presented on outbound calls
    outbound_sweep_interval_s: float = 5.0
    reminder_sweep_interval_s: float = 60.0

    # Phase 11 inbox. Meta app secret verifies WhatsApp webhook signatures (unset = accept in dev);
    # inbound_webhook_secret must match ``?secret=`` on carrier SMS webhooks when set.
    whatsapp_app_secret: str | None = None
    inbound_webhook_secret: str | None = None
    inbox_sla_minutes: int = 15
    inbox_sweep_interval_s: float = 60.0

    # Phase 17/18 ops & support. Sweep computes health/alerts/SLA; 0 disables. Pager routes via
    # the admin on-call config; Linear escalation is a seam (unset = log only).
    ops_sweep_interval_s: float = 300.0
    support_number: str | None = None  # E.164 support line answered by the support assistant
    linear_api_key: str | None = None
    linear_team_id: str | None = None

    # Phase 5b BYO SIP. "simulated" keeps trunks fully testable without a SIP edge.
    sip_provisioner: Literal["simulated", "livekit"] = "simulated"
    sip_domain: str = "sip.parlio.local"
    livekit_url: str | None = None
    # Browser-facing signalling URL (wss://) handed to dashboard/web-chat/site visitors; defaults to
    # livekit_url, which is wrong whenever the API reaches LiveKit over a private/Docker address.
    livekit_public_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
