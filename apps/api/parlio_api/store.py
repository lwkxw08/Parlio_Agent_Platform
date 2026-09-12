"""Phase 1 storage: in-memory maps mirrored to Redis when available.

Postgres (Supabase) with RLS replaces this in Phase 2; the interface stays the same.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from parlio_voice.models import AssistantConfig, CallEvent, CallEventType

log = logging.getLogger("parlio.api.store")


class CallRecord(BaseModel):
    call_id: str
    tenant_id: str
    company_id: str
    assistant_id: str
    caller: str | None = None
    dialed: str | None = None
    direction: str = "inbound"
    status: str = "ringing"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    answer_latency_s: float | None = None
    duration_s: float | None = None
    latency: dict[str, Any] = Field(default_factory=dict)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    recordings: list[str] = Field(default_factory=list)
    end_reason: str | None = None


class Store:
    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis
        self._assistants: dict[str, AssistantConfig] = {}
        self._number_to_assistant: dict[str, str] = {}
        self._calls: dict[str, CallRecord] = {}
        self._seen_events: set[str] = set()

    # -- assistants ---------------------------------------------------------------------------
    async def upsert_assistant(self, cfg: AssistantConfig, numbers: list[str]) -> None:
        self._assistants[cfg.assistant_id] = cfg
        for n in numbers:
            self._number_to_assistant[n] = cfg.assistant_id
        if self._redis is not None:
            try:
                await self._redis.set(f"parlio:assistant:{cfg.assistant_id}", cfg.model_dump_json())
                for n in numbers:
                    await self._redis.set(f"parlio:number:{n}", cfg.assistant_id)
                    # invalidate the worker-side cache so config changes apply on the next call
                    await self._redis.delete(f"parlio:assistant_config:{n}")
            except Exception:
                log.warning("redis write failed", exc_info=True)

    async def get_assistant(self, assistant_id: str) -> AssistantConfig | None:
        cfg = self._assistants.get(assistant_id)
        if cfg is None and self._redis is not None:
            raw = await self._redis.get(f"parlio:assistant:{assistant_id}")
            if raw:
                cfg = AssistantConfig.model_validate_json(raw)
                self._assistants[assistant_id] = cfg
        return cfg

    async def resolve_number(self, number: str) -> AssistantConfig | None:
        aid = self._number_to_assistant.get(number)
        if aid is None and self._redis is not None:
            raw = await self._redis.get(f"parlio:number:{number}")
            aid = raw.decode() if isinstance(raw, bytes) else raw
        return await self.get_assistant(aid) if aid else None

    def list_assistants(self) -> list[AssistantConfig]:
        return list(self._assistants.values())

    # -- calls --------------------------------------------------------------------------------
    def list_calls(self, tenant_id: str | None = None, limit: int = 50) -> list[CallRecord]:
        calls = [c for c in self._calls.values() if tenant_id is None or c.tenant_id == tenant_id]
        calls.sort(key=lambda c: c.started_at, reverse=True)
        return calls[:limit]

    def get_call(self, call_id: str) -> CallRecord | None:
        return self._calls.get(call_id)

    async def apply_event(self, ev: CallEvent) -> bool:
        """Fold a call event into the call record. Idempotent on event_id."""
        if ev.event_id in self._seen_events:
            return False
        self._seen_events.add(ev.event_id)

        call = self._calls.get(ev.call_id)
        if call is None:
            call = CallRecord(
                call_id=ev.call_id,
                tenant_id=ev.tenant_id,
                company_id=ev.company_id,
                assistant_id=ev.assistant_id,
                started_at=ev.occurred_at,
            )
            self._calls[ev.call_id] = call

        p = ev.payload
        match ev.type:
            case CallEventType.CALL_STARTED:
                call.caller = p.get("caller")
                call.dialed = p.get("dialed")
                call.direction = p.get("direction", "inbound")
            case CallEventType.CALL_ANSWERED:
                call.status = "in_progress"
                call.answered_at = ev.occurred_at
                call.answer_latency_s = p.get("answer_latency_s")
            case CallEventType.TRANSCRIPT_ITEM:
                call.transcript.append(
                    {"role": p.get("role"), "text": p.get("text"), "at": ev.occurred_at.isoformat()}
                )
            case CallEventType.RECORDING_STARTED:
                call.recordings = list(p.get("keys", []))
            case CallEventType.CALL_ENDED:
                call.status = "completed"
                call.ended_at = ev.occurred_at
                call.duration_s = p.get("duration_s")
                call.latency = p.get("latency", {})
                call.end_reason = p.get("reason")
                if p.get("transcript"):
                    call.transcript = p["transcript"]
                if p.get("recordings"):
                    call.recordings = p["recordings"]
            case CallEventType.CALL_FAILED:
                call.status = "failed"
                call.ended_at = ev.occurred_at
                call.end_reason = p.get("reason")
            case CallEventType.TURN_COMPLETED:
                pass
        return True
