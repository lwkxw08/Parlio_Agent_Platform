"""Publish call lifecycle events to the event bus (Redis Streams) with HTTP fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from redis.asyncio import Redis

from parlio_voice.models import AssistantConfig, CallEvent, CallEventType
from parlio_voice.settings import Settings

log = logging.getLogger("parlio.events")


class EventPublisher:
    def __init__(self, settings: Settings, redis: Redis | None) -> None:
        self._s = settings
        self._redis = redis
        self._http = httpx.AsyncClient(
            base_url=settings.api_url,
            headers={"X-Worker-Key": settings.worker_api_key},
            timeout=httpx.Timeout(3.0, connect=1.0),
        )
        self._pending: set[asyncio.Task[None]] = set()

    async def aclose(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        await self._http.aclose()

    def emit(
        self,
        cfg: AssistantConfig,
        call_id: str,
        type_: CallEventType,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget; never blocks the audio path."""
        ev = CallEvent(
            type=type_,
            call_id=call_id,
            tenant_id=cfg.tenant_id,
            company_id=cfg.company_id,
            assistant_id=cfg.assistant_id,
            payload=payload or {},
        )
        task = asyncio.create_task(self._publish(ev))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _publish(self, ev: CallEvent) -> None:
        body = ev.model_dump_json()
        if self._redis is not None:
            try:
                await self._redis.xadd(
                    self._s.events_stream,
                    {"type": ev.type.value, "call_id": ev.call_id, "body": body},
                    maxlen=100_000,
                    approximate=True,
                )
                return
            except Exception:
                log.warning("redis xadd failed, falling back to http", exc_info=True)
        try:
            r = await self._http.post(
                "/v1/worker/events", content=body, headers={"content-type": "application/json"}
            )
            r.raise_for_status()
        except httpx.HTTPError:
            log.error("event dropped: %s %s", ev.type, ev.call_id, exc_info=True)
