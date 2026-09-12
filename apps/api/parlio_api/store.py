"""Storage interface plus the in-memory implementation (mirrored to Redis when available).

`PostgresStore` (parlio_api.db.postgres) is the production implementation; both satisfy
`CallStore` so routes and the event consumer never care which one is active.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime
from typing import Any, Protocol

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
    # post-call pipeline output
    summary: str | None = None
    extracted: dict[str, Any] = Field(default_factory=dict)
    missed_fields: list[str] = Field(default_factory=list)
    caller_type: str | None = None  # new | returning | unknown
    contact_id: str | None = None


class PostCallResult(BaseModel):
    summary: str
    extracted: dict[str, Any] = Field(default_factory=dict)
    missed_fields: list[str] = Field(default_factory=list)
    caller_type: str = "unknown"
    contact_id: str | None = None


class RequiredField(BaseModel):
    name: str
    description: str = ""
    required: bool = True


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_worker_key() -> str:
    return f"pk_{secrets.token_urlsafe(32)}"


class CallStore(Protocol):
    async def upsert_assistant(self, cfg: AssistantConfig, numbers: list[str]) -> None: ...
    async def get_assistant(self, assistant_id: str) -> AssistantConfig | None: ...
    async def resolve_number(self, number: str) -> AssistantConfig | None: ...
    async def list_assistants(self, tenant_id: str | None = None) -> list[AssistantConfig]: ...
    async def list_calls(
        self, tenant_id: str | None = None, limit: int = 50
    ) -> list[CallRecord]: ...
    async def get_call(self, call_id: str) -> CallRecord | None: ...
    async def apply_event(self, ev: CallEvent) -> bool: ...
    async def record_postcall(self, call_id: str, result: PostCallResult) -> None: ...
    async def touch_contact(self, tenant_id: str, company_id: str, e164: str) -> tuple[str, bool]:
        """Upsert a contact by number; returns (contact_id, is_returning)."""
        ...

    async def required_fields(self, assistant_id: str) -> list[RequiredField]: ...
    async def set_required_fields(self, assistant_id: str, fields: list[RequiredField]) -> None: ...
    async def create_worker_key(self, tenant_id: str | None, name: str) -> str: ...
    async def verify_worker_key(self, key: str) -> bool: ...


def fold_event(call: CallRecord, ev: CallEvent) -> CallRecord:
    """Apply one lifecycle event to a call record (pure; shared by both stores)."""
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
    return call


class MemoryStore:
    """Dev/test store. Not durable; Redis mirror only serves worker config lookups."""

    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis
        self._assistants: dict[str, AssistantConfig] = {}
        self._number_to_assistant: dict[str, str] = {}
        self._calls: dict[str, CallRecord] = {}
        self._seen_events: set[str] = set()
        self._contacts: dict[tuple[str, str], tuple[str, int]] = {}
        self._required: dict[str, list[RequiredField]] = {}
        self._worker_keys: set[str] = set()

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

    async def list_assistants(self, tenant_id: str | None = None) -> list[AssistantConfig]:
        return [
            a for a in self._assistants.values() if tenant_id is None or a.tenant_id == tenant_id
        ]

    # -- calls --------------------------------------------------------------------------------
    async def list_calls(self, tenant_id: str | None = None, limit: int = 50) -> list[CallRecord]:
        calls = [c for c in self._calls.values() if tenant_id is None or c.tenant_id == tenant_id]
        calls.sort(key=lambda c: c.started_at, reverse=True)
        return calls[:limit]

    async def get_call(self, call_id: str) -> CallRecord | None:
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
        fold_event(call, ev)
        return True

    async def record_postcall(self, call_id: str, result: PostCallResult) -> None:
        call = self._calls.get(call_id)
        if call is None:
            return
        call.summary = result.summary
        call.extracted = result.extracted
        call.missed_fields = result.missed_fields
        call.caller_type = result.caller_type
        call.contact_id = result.contact_id

    # -- contacts / config --------------------------------------------------------------------
    async def touch_contact(self, tenant_id: str, company_id: str, e164: str) -> tuple[str, bool]:
        key = (tenant_id, e164)
        cid, seen = self._contacts.get(key, (f"contact-{len(self._contacts) + 1}", 0))
        self._contacts[key] = (cid, seen + 1)
        return cid, seen > 0

    async def required_fields(self, assistant_id: str) -> list[RequiredField]:
        return list(self._required.get(assistant_id, []))

    async def set_required_fields(self, assistant_id: str, fields: list[RequiredField]) -> None:
        self._required[assistant_id] = list(fields)

    # -- worker keys --------------------------------------------------------------------------
    async def create_worker_key(self, tenant_id: str | None, name: str) -> str:
        key = new_worker_key()
        self._worker_keys.add(hash_key(key))
        return key

    async def verify_worker_key(self, key: str) -> bool:
        return hash_key(key) in self._worker_keys
