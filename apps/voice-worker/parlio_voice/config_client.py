"""Resolve the AssistantConfig for an inbound call (Redis cache -> Core API -> demo)."""

from __future__ import annotations

import logging

import httpx
from redis.asyncio import Redis

from parlio_voice.models import AssistantConfig, Destination, Schedule, TransferConfig
from parlio_voice.settings import Settings

log = logging.getLogger("parlio.config")

DEMO_CONFIG = AssistantConfig(
    tenant_id="demo",
    company_id="demo",
    assistant_id="demo",
    name="Parlio",
    business_name="Parlio Demo Plumbing",
    instructions=(
        "You are {name}, the phone receptionist for {business_name}, a plumbing company in "
        "Manchester open Monday to Friday 8am to 6pm. Keep replies to one or two short "
        "sentences. Take the caller's name, phone number and the problem, and say a plumber "
        "will call back within the hour. Never quote prices."
    ),
    transfer=TransferConfig(
        destinations=[
            Destination(
                id="office",
                name="the office",
                department="general",
                address="+441614960000",
                fallback_id="oncall",
            ),
            Destination(
                id="oncall",
                name="the on-call plumber",
                department="emergencies",
                address="+447700900000",
                on_call=True,
                schedule=Schedule(always=True),
            ),
        ]
    ),
)


class ConfigClient:
    def __init__(self, settings: Settings, redis: Redis | None) -> None:
        self._s = settings
        self._redis = redis
        self._http = httpx.AsyncClient(
            base_url=settings.api_url,
            headers={"X-Worker-Key": settings.worker_api_key},
            timeout=httpx.Timeout(2.0, connect=1.0),
        )

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    async def aclose(self) -> None:
        await self._http.aclose()

    def _cache_key(self, dialed_number: str) -> str:
        return f"parlio:assistant_config:{dialed_number}"

    async def resolve(self, dialed_number: str) -> AssistantConfig:
        if self._redis is not None:
            try:
                raw = await self._redis.get(self._cache_key(dialed_number))
                if raw:
                    return AssistantConfig.model_validate_json(raw)
            except Exception:
                log.warning("redis cache read failed", exc_info=True)

        cfg = await self._fetch(dialed_number)
        if cfg is None:
            if not self._s.demo_mode:
                raise LookupError(f"no assistant configured for {dialed_number}")
            log.warning("no assistant for %s, using demo config", dialed_number)
            return DEMO_CONFIG

        if self._redis is not None:
            try:
                await self._redis.set(
                    self._cache_key(dialed_number),
                    cfg.model_dump_json(),
                    ex=self._s.config_cache_ttl_s,
                )
            except Exception:
                log.warning("redis cache write failed", exc_info=True)
        return cfg

    async def get(self, assistant_id: str) -> AssistantConfig:
        """Config by id (outbound jobs know their assistant; no number lookup involved)."""
        r = await self._http.get(f"/v1/worker/assistants/{assistant_id}")
        if r.status_code == 404:
            raise LookupError(f"assistant {assistant_id} not found")
        r.raise_for_status()
        return AssistantConfig.model_validate(r.json())

    async def _fetch(self, dialed_number: str) -> AssistantConfig | None:
        try:
            r = await self._http.get(
                "/v1/worker/assistants/resolve", params={"number": dialed_number}
            )
        except httpx.HTTPError:
            log.warning("core api unreachable", exc_info=True)
            return None
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return AssistantConfig.model_validate(r.json())
