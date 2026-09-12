"""Postgres implementation of `CallStore` (asyncpg via SQLAlchemy Core, plain SQL).

All tenant-scoped reads go through `tenant_tx(engine, tenant_id)` so RLS is enforced by the
database in addition to the WHERE clauses; worker/system paths pass `tenant_id=None`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from parlio_api.db.engine import tenant_tx
from parlio_api.store import (
    CallRecord,
    PostCallResult,
    RequiredField,
    fold_event,
    hash_key,
    new_worker_key,
)
from parlio_voice.models import AssistantConfig, CallEvent, CallEventType

log = logging.getLogger("parlio.api.store.pg")

_CALL_COLS = """
    id, organization_id, company_id, assistant_id, caller, dialed, direction, status,
    started_at, answered_at, ended_at, answer_latency_s, duration_s, latency, recordings,
    end_reason, summary, extracted, missed_fields, caller_type, contact_id
"""


def _jsonb(v: Any) -> str:
    return json.dumps(v)


def _row_to_call(r: Row[Any], transcript: list[dict[str, Any]]) -> CallRecord:
    m = r._mapping
    return CallRecord(
        call_id=m["id"],
        tenant_id=m["organization_id"],
        company_id=m["company_id"],
        assistant_id=m["assistant_id"],
        caller=m["caller"],
        dialed=m["dialed"],
        direction=m["direction"],
        status=m["status"],
        started_at=m["started_at"],
        answered_at=m["answered_at"],
        ended_at=m["ended_at"],
        answer_latency_s=m["answer_latency_s"],
        duration_s=m["duration_s"],
        latency=m["latency"] or {},
        transcript=transcript,
        recordings=m["recordings"] or [],
        end_reason=m["end_reason"],
        summary=m["summary"],
        extracted=m["extracted"] or {},
        missed_fields=m["missed_fields"] or [],
        caller_type=m["caller_type"],
        contact_id=m["contact_id"],
    )


class PostgresStore:
    def __init__(self, engine: AsyncEngine, redis: Redis | None = None) -> None:
        self._engine = engine
        self._redis = redis

    # -- assistants ---------------------------------------------------------------------------
    async def upsert_assistant(self, cfg: AssistantConfig, numbers: list[str]) -> None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, name) VALUES (:oid, :oid)"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"oid": cfg.tenant_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO companies (id, organization_id, name)"
                    " VALUES (:cid, :oid, :name) ON CONFLICT (id) DO NOTHING"
                ),
                {"cid": cfg.company_id, "oid": cfg.tenant_id, "name": cfg.business_name},
            )
            row = await conn.execute(
                text(
                    """
                    INSERT INTO assistants (id, organization_id, company_id, version, config)
                    VALUES (:aid, :oid, :cid, 1, CAST(:cfg AS jsonb))
                    ON CONFLICT (id) DO UPDATE SET
                        config = EXCLUDED.config,
                        version = assistants.version + 1,
                        updated_at = now()
                    RETURNING version
                    """
                ),
                {
                    "aid": cfg.assistant_id,
                    "oid": cfg.tenant_id,
                    "cid": cfg.company_id,
                    "cfg": cfg.model_dump_json(),
                },
            )
            version = row.scalar_one()
            await conn.execute(
                text(
                    """
                    INSERT INTO assistant_versions (assistant_id, organization_id, version, config)
                    VALUES (:aid, :oid, :v, CAST(:cfg AS jsonb))
                    ON CONFLICT (assistant_id, version) DO NOTHING
                    """
                ),
                {
                    "aid": cfg.assistant_id,
                    "oid": cfg.tenant_id,
                    "v": version,
                    "cfg": cfg.model_dump_json(),
                },
            )
            for n in numbers:
                await conn.execute(
                    text(
                        """
                        INSERT INTO phone_numbers (e164, organization_id, company_id, assistant_id)
                        VALUES (:n, :oid, :cid, :aid)
                        ON CONFLICT (e164) DO UPDATE SET
                            assistant_id = EXCLUDED.assistant_id,
                            organization_id = EXCLUDED.organization_id,
                            company_id = EXCLUDED.company_id
                        """
                    ),
                    {"n": n, "oid": cfg.tenant_id, "cid": cfg.company_id, "aid": cfg.assistant_id},
                )
        if self._redis is not None:
            try:
                for n in numbers:
                    await self._redis.delete(f"parlio:assistant_config:{n}")
            except Exception:
                log.warning("redis invalidate failed", exc_info=True)

    async def get_assistant(self, assistant_id: str) -> AssistantConfig | None:
        async with tenant_tx(self._engine, None) as conn:
            raw = (
                await conn.execute(
                    text("SELECT config FROM assistants WHERE id = :aid"), {"aid": assistant_id}
                )
            ).scalar_one_or_none()
        return AssistantConfig.model_validate(raw) if raw is not None else None

    async def resolve_number(self, number: str) -> AssistantConfig | None:
        async with tenant_tx(self._engine, None) as conn:
            raw = (
                await conn.execute(
                    text(
                        "SELECT a.config FROM phone_numbers p JOIN assistants a"
                        " ON a.id = p.assistant_id WHERE p.e164 = :n"
                    ),
                    {"n": number},
                )
            ).scalar_one_or_none()
        return AssistantConfig.model_validate(raw) if raw is not None else None

    async def list_assistants(self, tenant_id: str | None = None) -> list[AssistantConfig]:
        async with tenant_tx(self._engine, tenant_id) as conn:
            rows = await conn.execute(
                text(
                    "SELECT config FROM assistants"
                    " WHERE (CAST(:tid AS text) IS NULL OR organization_id = :tid)"
                    " ORDER BY created_at"
                ),
                {"tid": tenant_id},
            )
        return [AssistantConfig.model_validate(r[0]) for r in rows]

    # -- calls --------------------------------------------------------------------------------
    async def _transcript(self, conn: AsyncConnection, call_id: str) -> list[dict[str, Any]]:
        rows = await conn.execute(
            text(
                "SELECT role, text, at, interrupted FROM transcripts"
                " WHERE call_id = :cid ORDER BY seq"
            ),
            {"cid": call_id},
        )
        return [
            {
                "role": r.role,
                "text": r.text,
                "at": r.at.isoformat() if r.at else None,
                "interrupted": r.interrupted,
            }
            for r in rows
        ]

    async def list_calls(self, tenant_id: str | None = None, limit: int = 50) -> list[CallRecord]:
        async with tenant_tx(self._engine, tenant_id) as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {_CALL_COLS} FROM calls"
                        " WHERE (CAST(:tid AS text) IS NULL OR organization_id = :tid)"
                        " ORDER BY started_at DESC LIMIT :lim"
                    ),
                    {"tid": tenant_id, "lim": limit},
                )
            ).all()
            return [_row_to_call(r, await self._transcript(conn, r.id)) for r in rows]

    async def get_call(self, call_id: str) -> CallRecord | None:
        async with tenant_tx(self._engine, None) as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_CALL_COLS} FROM calls WHERE id = :cid"), {"cid": call_id}
                )
            ).first()
            if row is None:
                return None
            return _row_to_call(row, await self._transcript(conn, call_id))

    async def apply_event(self, ev: CallEvent) -> bool:
        async with tenant_tx(self._engine, None) as conn:
            inserted = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO call_events
                            (event_id, organization_id, call_id, type, occurred_at)
                        VALUES (:eid, :oid, :cid, :type, :at)
                        ON CONFLICT (event_id) DO NOTHING RETURNING event_id
                        """
                    ),
                    {
                        "eid": ev.event_id,
                        "oid": ev.tenant_id,
                        "cid": ev.call_id,
                        "type": ev.type.value,
                        "at": ev.occurred_at,
                    },
                )
            ).first()
            if inserted is None:
                return False

            row = (
                await conn.execute(
                    text(f"SELECT {_CALL_COLS} FROM calls WHERE id = :cid FOR UPDATE"),
                    {"cid": ev.call_id},
                )
            ).first()
            if row is None:
                call = CallRecord(
                    call_id=ev.call_id,
                    tenant_id=ev.tenant_id,
                    company_id=ev.company_id,
                    assistant_id=ev.assistant_id,
                    started_at=ev.occurred_at,
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO calls
                            (id, organization_id, company_id, assistant_id, started_at)
                        VALUES (:cid, :oid, :co, :aid, :at)
                        """
                    ),
                    {
                        "cid": call.call_id,
                        "oid": call.tenant_id,
                        "co": call.company_id,
                        "aid": call.assistant_id,
                        "at": call.started_at,
                    },
                )
            else:
                call = _row_to_call(row, [])

            fold_event(call, ev)
            await conn.execute(
                text(
                    """
                    UPDATE calls SET
                        caller = :caller, dialed = :dialed, direction = :direction,
                        status = :status, answered_at = :answered_at, ended_at = :ended_at,
                        answer_latency_s = :als, duration_s = :dur,
                        latency = CAST(:latency AS jsonb), recordings = CAST(:recordings AS jsonb),
                        end_reason = :end_reason
                    WHERE id = :cid
                    """
                ),
                {
                    "cid": call.call_id,
                    "caller": call.caller,
                    "dialed": call.dialed,
                    "direction": call.direction,
                    "status": call.status,
                    "answered_at": call.answered_at,
                    "ended_at": call.ended_at,
                    "als": call.answer_latency_s,
                    "dur": call.duration_s,
                    "latency": _jsonb(call.latency),
                    "recordings": _jsonb(call.recordings),
                    "end_reason": call.end_reason,
                },
            )

            if ev.type == CallEventType.TRANSCRIPT_ITEM:
                await conn.execute(
                    text(
                        """
                        INSERT INTO transcripts
                            (call_id, organization_id, started_at, seq, role, text, at, interrupted)
                        SELECT :cid, :oid, :started, COALESCE(MAX(seq), 0) + 1, :role, :text, :at,
                               :interrupted
                        FROM transcripts WHERE call_id = :cid
                        """
                    ),
                    {
                        "cid": call.call_id,
                        "oid": call.tenant_id,
                        "started": call.started_at,
                        "role": ev.payload.get("role", "unknown"),
                        "text": ev.payload.get("text") or "",
                        "at": ev.occurred_at,
                        "interrupted": bool(ev.payload.get("interrupted", False)),
                    },
                )
            elif ev.type == CallEventType.CALL_ENDED and ev.payload.get("transcript"):
                await self._replace_transcript(conn, call, ev.payload["transcript"])
            return True

    async def _replace_transcript(
        self, conn: AsyncConnection, call: CallRecord, items: list[dict[str, Any]]
    ) -> None:
        await conn.execute(
            text("DELETE FROM transcripts WHERE call_id = :cid"), {"cid": call.call_id}
        )
        for i, item in enumerate(items, start=1):
            at = item.get("at")
            await conn.execute(
                text(
                    """
                    INSERT INTO transcripts
                        (call_id, organization_id, started_at, seq, role, text, at, interrupted)
                    VALUES (:cid, :oid, :started, :seq, :role, :text, :at, :interrupted)
                    """
                ),
                {
                    "cid": call.call_id,
                    "oid": call.tenant_id,
                    "started": call.started_at,
                    "seq": i,
                    "role": item.get("role", "unknown"),
                    "text": item.get("text") or "",
                    "at": datetime.fromisoformat(at) if isinstance(at, str) else None,
                    "interrupted": bool(item.get("interrupted", False)),
                },
            )

    async def record_postcall(self, call_id: str, result: PostCallResult) -> None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text(
                    """
                    UPDATE calls SET summary = :summary, extracted = CAST(:extracted AS jsonb),
                        missed_fields = CAST(:missed AS jsonb), caller_type = :caller_type,
                        contact_id = :contact_id
                    WHERE id = :cid
                    """
                ),
                {
                    "cid": call_id,
                    "summary": result.summary,
                    "extracted": _jsonb(result.extracted),
                    "missed": _jsonb(result.missed_fields),
                    "caller_type": result.caller_type,
                    "contact_id": result.contact_id,
                },
            )

    # -- contacts / config --------------------------------------------------------------------
    async def touch_contact(self, tenant_id: str, company_id: str, e164: str) -> tuple[str, bool]:
        async with tenant_tx(self._engine, None) as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO contacts (id, organization_id, company_id, e164, call_count)
                        VALUES (:id, :oid, :cid, :e164, 1)
                        ON CONFLICT (organization_id, e164) DO UPDATE SET
                            call_count = contacts.call_count + 1, last_seen_at = now()
                        RETURNING id, call_count
                        """
                    ),
                    {"id": f"ct_{uuid4().hex}", "oid": tenant_id, "cid": company_id, "e164": e164},
                )
            ).one()
        return row.id, row.call_count > 1

    async def required_fields(self, assistant_id: str) -> list[RequiredField]:
        async with tenant_tx(self._engine, None) as conn:
            rows = await conn.execute(
                text(
                    "SELECT name, description, required FROM required_fields"
                    " WHERE assistant_id = :aid ORDER BY position, name"
                ),
                {"aid": assistant_id},
            )
        return [
            RequiredField(name=r.name, description=r.description, required=r.required) for r in rows
        ]

    async def set_required_fields(self, assistant_id: str, fields: list[RequiredField]) -> None:
        async with tenant_tx(self._engine, None) as conn:
            oid = (
                await conn.execute(
                    text("SELECT organization_id FROM assistants WHERE id = :aid"),
                    {"aid": assistant_id},
                )
            ).scalar_one_or_none()
            if oid is None:
                raise KeyError(assistant_id)
            await conn.execute(
                text("DELETE FROM required_fields WHERE assistant_id = :aid"), {"aid": assistant_id}
            )
            for i, f in enumerate(fields):
                await conn.execute(
                    text(
                        """
                        INSERT INTO required_fields
                            (organization_id, assistant_id, name, description, required, position)
                        VALUES (:oid, :aid, :name, :desc, :req, :pos)
                        """
                    ),
                    {
                        "oid": oid,
                        "aid": assistant_id,
                        "name": f.name,
                        "desc": f.description,
                        "req": f.required,
                        "pos": i,
                    },
                )

    # -- worker keys --------------------------------------------------------------------------
    async def create_worker_key(self, tenant_id: str | None, name: str) -> str:
        key = new_worker_key()
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text(
                    "INSERT INTO worker_api_keys (id, organization_id, name, key_hash)"
                    " VALUES (:id, :oid, :name, :h)"
                ),
                {"id": f"wk_{uuid4().hex}", "oid": tenant_id, "name": name, "h": hash_key(key)},
            )
        return key

    async def verify_worker_key(self, key: str) -> bool:
        async with tenant_tx(self._engine, None) as conn:
            found = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM worker_api_keys WHERE key_hash = :h AND revoked_at IS NULL"
                    ),
                    {"h": hash_key(key)},
                )
            ).first()
        return found is not None

    async def ensure_partitions(self) -> None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(text("SELECT ensure_month_partitions(3)"))
