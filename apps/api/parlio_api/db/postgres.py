"""Postgres implementation of `CallStore` (asyncpg via SQLAlchemy Core, plain SQL).

All tenant-scoped reads go through `tenant_tx(engine, tenant_id)` so RLS is enforced by the
database in addition to the WHERE clauses; worker/system paths pass `tenant_id=None`.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from parlio_api.db.engine import tenant_tx
from parlio_api.store import (
    AssistantVersion,
    CallFeedback,
    CallFilter,
    CallRecord,
    Contact,
    ContactUpdate,
    Member,
    PostCallResult,
    RequiredField,
    TenantDoc,
    Ticket,
    TicketEvent,
    TicketStats,
    TicketStatus,
    TicketUpdate,
    TransferRecord,
    TransferStats,
    apply_ticket_update,
    compute_ticket_stats,
    compute_transfer_stats,
    fold_event,
    hash_key,
    new_worker_key,
    transfer_from_event,
)
from parlio_voice.models import AssistantConfig, CallEvent, CallEventType

log = logging.getLogger("parlio.api.store.pg")

_CALL_COLS = """
    id, organization_id, company_id, assistant_id, caller, dialed, direction, status,
    started_at, answered_at, ended_at, answer_latency_s, duration_s, latency, recordings,
    end_reason, summary, extracted, missed_fields, caller_type, contact_id,
    escalated, escalation_keyword, read, feedback, share_token
"""

_CONTACT_COLS = """
    id, organization_id, company_id, e164, name, email, vip, notes, status, call_count,
    first_seen_at, last_seen_at
"""

_TICKET_COLS = """
    id, organization_id, company_id, call_id, contact_id, status, priority, category, department,
    caller_name, caller_number, reason, callback_window, source, sla_due_at, sla_breached,
    assigned_to, created_at, updated_at, resolved_at
"""

_TRANSFER_COLS = """
    id, organization_id, call_id, destination, destination_id, department, mode, outcome, reason,
    started_at, ended_at
"""


def _row_to_ticket(r: Row[Any]) -> Ticket:
    m = r._mapping
    return Ticket(
        id=m["id"],
        tenant_id=m["organization_id"],
        company_id=m["company_id"],
        call_id=m["call_id"],
        contact_id=m["contact_id"],
        status=m["status"],
        priority=m["priority"],
        category=m["category"],
        department=m["department"],
        caller_name=m["caller_name"],
        caller_number=m["caller_number"],
        reason=m["reason"] or "",
        callback_window=m["callback_window"],
        source=m["source"],
        sla_due_at=m["sla_due_at"],
        sla_breached=m["sla_breached"],
        assigned_to=m["assigned_to"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
        resolved_at=m["resolved_at"],
    )


def _row_to_transfer(r: Row[Any]) -> TransferRecord:
    m = r._mapping
    return TransferRecord(
        id=m["id"],
        tenant_id=m["organization_id"],
        call_id=m["call_id"],
        destination=m["destination"],
        destination_id=m["destination_id"],
        department=m["department"],
        mode=m["mode"],
        outcome=m["outcome"] or "no_answer",
        reason=m["reason"],
        started_at=m["started_at"],
        ended_at=m["ended_at"],
    )


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
        escalated=bool(m["escalated"]),
        escalation_keyword=m["escalation_keyword"],
        read=bool(m["read"]),
        feedback=m["feedback"] or [],
        share_token=m["share_token"],
    )


def _row_to_member(r: Row[Any]) -> Member:
    return Member(
        tenant_id=r.organization_id,
        user_id=r.user_id,
        email=r.email,
        name=r.name,
        role=r.role,
        status=r.status,
        invited_at=r.invited_at,
    )


def _row_to_doc(r: Row[Any]) -> TenantDoc:
    return TenantDoc(
        kind=r.kind,
        id=r.id,
        tenant_id=r.organization_id,
        data=r.data if isinstance(r.data, dict) else json.loads(r.data),
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _row_to_contact(r: Row[Any]) -> Contact:
    m = r._mapping
    return Contact(
        id=m["id"],
        tenant_id=m["organization_id"],
        company_id=m["company_id"],
        e164=m["e164"],
        name=m["name"],
        email=m["email"],
        vip=bool(m["vip"]),
        notes=m["notes"],
        status=m["status"],
        call_count=m["call_count"],
        first_seen_at=m["first_seen_at"],
        last_seen_at=m["last_seen_at"],
    )


async def _hydrate_handoff(conn: AsyncConnection, calls: list[CallRecord]) -> None:
    """Attach transfers/ticket ids (kept in their own tables) to call records."""
    if not calls:
        return
    by_id = {c.call_id: c for c in calls}
    ids = list(by_id)
    rows = await conn.execute(
        text(
            "SELECT call_id, id, destination, department, mode, outcome, reason"
            " FROM transfers WHERE call_id = ANY(:ids) ORDER BY started_at"
        ),
        {"ids": ids},
    )
    for r in rows:
        by_id[r.call_id].transfers.append(
            {
                "transfer_id": r.id,
                "destination": r.destination,
                "department": r.department,
                "mode": r.mode,
                "outcome": r.outcome,
                "reason": r.reason,
            }
        )
    rows = await conn.execute(
        text("SELECT call_id, id FROM tickets WHERE call_id = ANY(:ids) ORDER BY created_at"),
        {"ids": ids},
    )
    for r in rows:
        by_id[r.call_id].ticket_ids.append(r.id)


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
            prev = (
                await conn.execute(
                    text("SELECT version FROM assistants WHERE id = :aid FOR UPDATE"),
                    {"aid": cfg.assistant_id},
                )
            ).scalar_one_or_none()
            version = (prev or 0) + 1
            cfg.assistant_version = version
            await conn.execute(
                text(
                    """
                    INSERT INTO assistants (id, organization_id, company_id, version, config)
                    VALUES (:aid, :oid, :cid, :v, CAST(:cfg AS jsonb))
                    ON CONFLICT (id) DO UPDATE SET
                        config = EXCLUDED.config,
                        version = EXCLUDED.version,
                        updated_at = now()
                    """
                ),
                {
                    "aid": cfg.assistant_id,
                    "oid": cfg.tenant_id,
                    "cid": cfg.company_id,
                    "v": version,
                    "cfg": cfg.model_dump_json(),
                },
            )
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

    async def list_assistant_versions(self, assistant_id: str) -> list[AssistantVersion]:
        async with tenant_tx(self._engine, None) as conn:
            rows = await conn.execute(
                text(
                    "SELECT version, config, created_by, created_at, note FROM assistant_versions"
                    " WHERE assistant_id = :aid ORDER BY version DESC"
                ),
                {"aid": assistant_id},
            )
            return [
                AssistantVersion(
                    assistant_id=assistant_id,
                    version=r.version,
                    created_at=r.created_at,
                    created_by=r.created_by,
                    note=r.note,
                    config=AssistantConfig.model_validate(r.config),
                )
                for r in rows
            ]

    async def get_assistant_version(
        self, assistant_id: str, version: int
    ) -> AssistantConfig | None:
        async with tenant_tx(self._engine, None) as conn:
            raw = (
                await conn.execute(
                    text(
                        "SELECT config FROM assistant_versions"
                        " WHERE assistant_id = :aid AND version = :v"
                    ),
                    {"aid": assistant_id, "v": version},
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
            calls = [_row_to_call(r, await self._transcript(conn, r.id)) for r in rows]
            await _hydrate_handoff(conn, calls)
            return calls

    async def filter_calls(self, f: CallFilter) -> list[CallRecord]:
        # SQL narrows by tenant/time; the derived `kind` bucket is applied in Python
        async with tenant_tx(self._engine, f.tenant_id) as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {_CALL_COLS} FROM calls"
                        " WHERE (CAST(:tid AS text) IS NULL OR organization_id = :tid)"
                        " AND (CAST(:since AS timestamptz) IS NULL OR started_at >= :since)"
                        " AND (CAST(:until AS timestamptz) IS NULL OR started_at < :until)"
                        " ORDER BY started_at DESC LIMIT :lim"
                    ),
                    {"tid": f.tenant_id, "since": f.since, "until": f.until, "lim": f.limit * 5},
                )
            ).all()
            calls = [_row_to_call(r, []) for r in rows]
            await _hydrate_handoff(conn, calls)
        return [c for c in calls if f.matches(c)][: f.limit]

    async def _get_call(
        self, conn: AsyncConnection, where: str, params: dict[str, Any]
    ) -> CallRecord | None:
        row = (
            await conn.execute(text(f"SELECT {_CALL_COLS} FROM calls WHERE {where}"), params)
        ).first()
        if row is None:
            return None
        call = _row_to_call(row, await self._transcript(conn, row.id))
        await _hydrate_handoff(conn, [call])
        return call

    async def get_call(self, call_id: str) -> CallRecord | None:
        async with tenant_tx(self._engine, None) as conn:
            return await self._get_call(conn, "id = :cid", {"cid": call_id})

    async def get_call_by_share_token(self, token: str) -> CallRecord | None:
        async with tenant_tx(self._engine, None) as conn:
            return await self._get_call(conn, "share_token = :tok", {"tok": token})

    async def mark_call_read(self, call_id: str, read: bool = True) -> CallRecord | None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text("UPDATE calls SET read = :read WHERE id = :cid"),
                {"cid": call_id, "read": read},
            )
            return await self._get_call(conn, "id = :cid", {"cid": call_id})

    async def add_call_feedback(self, call_id: str, fb: CallFeedback) -> CallRecord | None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text("UPDATE calls SET feedback = feedback || CAST(:fb AS jsonb) WHERE id = :cid"),
                {"cid": call_id, "fb": _jsonb([fb.model_dump(mode="json")])},
            )
            return await self._get_call(conn, "id = :cid", {"cid": call_id})

    async def ensure_share_token(self, call_id: str) -> str | None:
        async with tenant_tx(self._engine, None) as conn:
            row = (
                await conn.execute(
                    text(
                        "UPDATE calls SET share_token = COALESCE(share_token, :tok)"
                        " WHERE id = :cid RETURNING share_token"
                    ),
                    {"cid": call_id, "tok": secrets.token_urlsafe(16)},
                )
            ).first()
        return row.share_token if row else None

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
                        end_reason = :end_reason, escalated = :escalated,
                        escalation_keyword = :esc_kw
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
                    "escalated": call.escalated,
                    "esc_kw": call.escalation_keyword,
                },
            )

            tr = transfer_from_event(ev)
            if tr is not None:
                await conn.execute(
                    text(
                        f"""
                        INSERT INTO transfers ({_TRANSFER_COLS})
                        VALUES (:id, :oid, :cid, :dest, :dest_id, :dept, :mode, :outcome, :reason,
                                :started, :ended)
                        ON CONFLICT (id) DO UPDATE SET outcome = EXCLUDED.outcome,
                            ended_at = EXCLUDED.ended_at, reason = EXCLUDED.reason
                        """
                    ),
                    {
                        "id": tr.id,
                        "oid": tr.tenant_id,
                        "cid": tr.call_id,
                        "dest": tr.destination,
                        "dest_id": tr.destination_id,
                        "dept": tr.department,
                        "mode": tr.mode,
                        "outcome": tr.outcome,
                        "reason": tr.reason,
                        "started": tr.started_at,
                        "ended": tr.ended_at,
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

    async def list_contacts(
        self, tenant_id: str | None = None, q: str | None = None, limit: int = 200
    ) -> list[Contact]:
        async with tenant_tx(self._engine, tenant_id) as conn:
            rows = await conn.execute(
                text(
                    f"SELECT {_CONTACT_COLS} FROM contacts"
                    " WHERE (CAST(:tid AS text) IS NULL OR organization_id = :tid)"
                    " AND (CAST(:pat AS text) IS NULL OR e164 ILIKE :pat OR name ILIKE :pat"
                    "      OR email ILIKE :pat)"
                    " ORDER BY last_seen_at DESC LIMIT :lim"
                ),
                {"tid": tenant_id, "pat": f"%{q}%" if q else None, "lim": limit},
            )
            return [_row_to_contact(r) for r in rows]

    async def get_contact(self, contact_id: str) -> Contact | None:
        async with tenant_tx(self._engine, None) as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_CONTACT_COLS} FROM contacts WHERE id = :id"), {"id": contact_id}
                )
            ).first()
        return _row_to_contact(row) if row else None

    async def update_contact(self, contact_id: str, upd: ContactUpdate) -> Contact | None:
        fields = upd.model_dump(exclude_none=True)
        if fields:
            sets = ", ".join(f"{k} = :{k}" for k in fields)
            async with tenant_tx(self._engine, None) as conn:
                await conn.execute(
                    text(f"UPDATE contacts SET {sets} WHERE id = :id"), {"id": contact_id, **fields}
                )
        return await self.get_contact(contact_id)

    # -- members ------------------------------------------------------------------------------
    async def list_members(self, tenant_id: str) -> list[Member]:
        async with tenant_tx(self._engine, tenant_id) as conn:
            rows = await conn.execute(
                text(
                    "SELECT m.organization_id, m.user_id, u.email, u.name, m.role, m.status,"
                    " m.invited_at FROM memberships m JOIN users u ON u.id = m.user_id"
                    " WHERE m.organization_id = :tid ORDER BY u.email"
                ),
                {"tid": tenant_id},
            )
            return [_row_to_member(r) for r in rows]

    async def upsert_member(self, m: Member) -> Member:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text("INSERT INTO organizations (id, name) VALUES (:t, :t) ON CONFLICT DO NOTHING"),
                {"t": m.tenant_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, name) VALUES (:uid, :email, :name)"
                    " ON CONFLICT (email) DO UPDATE SET name = COALESCE(EXCLUDED.name, users.name)"
                ),
                {"uid": m.user_id, "email": m.email.lower(), "name": m.name},
            )
            uid = (
                await conn.execute(
                    text("SELECT id FROM users WHERE email = :email"), {"email": m.email.lower()}
                )
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO memberships (user_id, organization_id, role, status, invited_at)"
                    " VALUES (:uid, :tid, :role, :status, :inv)"
                    " ON CONFLICT (organization_id, user_id) DO UPDATE SET role = EXCLUDED.role,"
                    " status = EXCLUDED.status"
                ),
                {
                    "uid": uid,
                    "tid": m.tenant_id,
                    "role": m.role,
                    "status": m.status,
                    "inv": m.invited_at,
                },
            )
        return m.model_copy(update={"user_id": uid, "email": m.email.lower()})

    async def remove_member(self, tenant_id: str, user_id: str) -> bool:
        async with tenant_tx(self._engine, None) as conn:
            res = await conn.execute(
                text("DELETE FROM memberships WHERE organization_id = :tid AND user_id = :uid"),
                {"tid": tenant_id, "uid": user_id},
            )
        return bool(res.rowcount)

    async def memberships_for_email(self, email: str) -> list[Member]:
        async with tenant_tx(self._engine, None) as conn:
            rows = await conn.execute(
                text(
                    "SELECT m.organization_id, m.user_id, u.email, u.name, m.role, m.status,"
                    " m.invited_at FROM memberships m JOIN users u ON u.id = m.user_id"
                    " WHERE u.email = :email"
                ),
                {"email": email.lower()},
            )
            return [_row_to_member(r) for r in rows]

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

    # -- transfers ----------------------------------------------------------------------------
    async def list_transfers(
        self, tenant_id: str | None = None, limit: int = 100
    ) -> list[TransferRecord]:
        async with tenant_tx(self._engine, tenant_id) as conn:
            rows = await conn.execute(
                text(
                    f"""
                    SELECT {_TRANSFER_COLS} FROM transfers
                    WHERE (CAST(:tid AS text) IS NULL OR organization_id = :tid)
                    ORDER BY started_at DESC LIMIT :lim
                    """
                ),
                {"tid": tenant_id, "lim": limit},
            )
            return [_row_to_transfer(r) for r in rows]

    async def transfer_stats(self, tenant_id: str | None = None) -> TransferStats:
        return compute_transfer_stats(await self.list_transfers(tenant_id, limit=100_000))

    # -- tickets ------------------------------------------------------------------------------
    async def create_ticket(self, ticket: Ticket) -> Ticket:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text(
                    f"""
                    INSERT INTO tickets ({_TICKET_COLS})
                    VALUES (:id, :oid, :co, :call_id, :contact_id, :status, :priority, :category,
                            :department, :caller_name, :caller_number, :reason, :callback_window,
                            :source, :sla_due_at, :sla_breached, :assigned_to, :created_at,
                            :updated_at, :resolved_at)
                    """
                ),
                self._ticket_params(ticket),
            )
            await self._insert_ticket_event(
                conn,
                ticket.tenant_id,
                TicketEvent(
                    ticket_id=ticket.id, type="created", note=ticket.source, at=ticket.created_at
                ),
            )
        return ticket

    @staticmethod
    def _ticket_params(t: Ticket) -> dict[str, Any]:
        return {
            "id": t.id,
            "oid": t.tenant_id,
            "co": t.company_id,
            "call_id": t.call_id,
            "contact_id": t.contact_id,
            "status": t.status.value,
            "priority": t.priority.value,
            "category": t.category,
            "department": t.department,
            "caller_name": t.caller_name,
            "caller_number": t.caller_number,
            "reason": t.reason,
            "callback_window": t.callback_window,
            "source": t.source,
            "sla_due_at": t.sla_due_at,
            "sla_breached": t.sla_breached,
            "assigned_to": t.assigned_to,
            "created_at": t.created_at,
            "updated_at": t.updated_at,
            "resolved_at": t.resolved_at,
        }

    @staticmethod
    async def _insert_ticket_event(conn: AsyncConnection, tenant_id: str, ev: TicketEvent) -> None:
        await conn.execute(
            text(
                """
                INSERT INTO ticket_events
                    (organization_id, ticket_id, type, actor, note, created_at)
                VALUES (:oid, :tid, :type, :actor, :note, :at)
                """
            ),
            {
                "oid": tenant_id,
                "tid": ev.ticket_id,
                "type": ev.type,
                "actor": ev.actor,
                "note": ev.note,
                "at": ev.at,
            },
        )

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        async with tenant_tx(self._engine, None) as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_TICKET_COLS} FROM tickets WHERE id = :id"), {"id": ticket_id}
                )
            ).first()
            return _row_to_ticket(row) if row else None

    async def list_tickets(
        self,
        tenant_id: str | None = None,
        status: TicketStatus | None = None,
        limit: int = 100,
    ) -> list[Ticket]:
        async with tenant_tx(self._engine, tenant_id) as conn:
            rows = await conn.execute(
                text(
                    f"""
                    SELECT {_TICKET_COLS} FROM tickets
                    WHERE (CAST(:tid AS text) IS NULL OR organization_id = :tid)
                      AND (CAST(:st AS text) IS NULL OR status = :st)
                    ORDER BY created_at DESC LIMIT :lim
                    """
                ),
                {"tid": tenant_id, "st": status.value if status else None, "lim": limit},
            )
            return [_row_to_ticket(r) for r in rows]

    async def update_ticket(self, ticket_id: str, upd: TicketUpdate) -> Ticket | None:
        async with tenant_tx(self._engine, None) as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_TICKET_COLS} FROM tickets WHERE id = :id FOR UPDATE"),
                    {"id": ticket_id},
                )
            ).first()
            if row is None:
                return None
            t = _row_to_ticket(row)
            events = apply_ticket_update(t, upd, datetime.now(UTC))
            await conn.execute(
                text(
                    """
                    UPDATE tickets SET status = :status, priority = :priority,
                        assigned_to = :assigned_to, updated_at = :updated_at,
                        resolved_at = :resolved_at
                    WHERE id = :id
                    """
                ),
                self._ticket_params(t),
            )
            for ev in events:
                await self._insert_ticket_event(conn, t.tenant_id, ev)
            return t

    async def add_ticket_event(self, ev: TicketEvent) -> None:
        async with tenant_tx(self._engine, None) as conn:
            oid = (
                await conn.execute(
                    text("SELECT organization_id FROM tickets WHERE id = :id"), {"id": ev.ticket_id}
                )
            ).scalar_one_or_none()
            if oid is None:
                return
            await self._insert_ticket_event(conn, oid, ev)

    async def ticket_events(self, ticket_id: str) -> list[TicketEvent]:
        async with tenant_tx(self._engine, None) as conn:
            rows = await conn.execute(
                text(
                    """
                    SELECT ticket_id, type, actor, note, created_at FROM ticket_events
                    WHERE ticket_id = :id ORDER BY id
                    """
                ),
                {"id": ticket_id},
            )
            return [
                TicketEvent(
                    ticket_id=r._mapping["ticket_id"],
                    type=r._mapping["type"],
                    actor=r._mapping["actor"],
                    note=r._mapping["note"],
                    at=r._mapping["created_at"],
                )
                for r in rows
            ]

    async def overdue_tickets(self, now: datetime) -> list[Ticket]:
        async with tenant_tx(self._engine, None) as conn:
            rows = await conn.execute(
                text(
                    f"""
                    SELECT {_TICKET_COLS} FROM tickets
                    WHERE status IN ('open', 'claimed') AND sla_breached = false
                      AND sla_due_at IS NOT NULL AND sla_due_at <= :now
                    """
                ),
                {"now": now},
            )
            return [_row_to_ticket(r) for r in rows]

    async def mark_sla_breached(self, ticket_id: str) -> None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text("UPDATE tickets SET sla_breached = true, updated_at = now() WHERE id = :id"),
                {"id": ticket_id},
            )

    async def ticket_stats(self, tenant_id: str | None = None) -> TicketStats:
        tickets = await self.list_tickets(tenant_id, limit=100_000)
        async with tenant_tx(self._engine, tenant_id) as conn:
            rows = await conn.execute(
                text(
                    """
                    SELECT ticket_id, type, actor, note, created_at FROM ticket_events
                    WHERE type = 'claimed'
                      AND (CAST(:tid AS text) IS NULL OR organization_id = :tid)
                    ORDER BY id
                    """
                ),
                {"tid": tenant_id},
            )
            events = [
                TicketEvent(
                    ticket_id=r._mapping["ticket_id"],
                    type=r._mapping["type"],
                    actor=r._mapping["actor"],
                    note=r._mapping["note"],
                    at=r._mapping["created_at"],
                )
                for r in rows
            ]
        return compute_ticket_stats(tickets, events)

    # -- tenant documents ---------------------------------------------------------------------
    async def put_doc(self, doc: TenantDoc) -> TenantDoc:
        async with tenant_tx(self._engine, doc.tenant_id) as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO tenant_documents (kind, id, organization_id, data)
                        VALUES (:kind, :id, :tid, CAST(:data AS jsonb))
                        ON CONFLICT (kind, id) DO UPDATE SET
                            data = EXCLUDED.data, updated_at = now()
                        RETURNING created_at, updated_at
                        """
                    ),
                    {
                        "kind": doc.kind,
                        "id": doc.id,
                        "tid": doc.tenant_id,
                        "data": json.dumps(doc.data, default=str),
                    },
                )
            ).one()
        return doc.model_copy(update={"created_at": row.created_at, "updated_at": row.updated_at})

    async def get_doc(self, kind: str, doc_id: str) -> TenantDoc | None:
        async with tenant_tx(self._engine, None) as conn:
            r = (
                await conn.execute(
                    text(
                        "SELECT kind, id, organization_id, data, created_at, updated_at"
                        " FROM tenant_documents WHERE kind = :k AND id = :id"
                    ),
                    {"k": kind, "id": doc_id},
                )
            ).first()
        return _row_to_doc(r) if r else None

    async def list_docs(
        self, kind: str, tenant_id: str | None = None, limit: int = 200
    ) -> list[TenantDoc]:
        async with tenant_tx(self._engine, tenant_id) as conn:
            rows = await conn.execute(
                text(
                    "SELECT kind, id, organization_id, data, created_at, updated_at"
                    " FROM tenant_documents WHERE kind = :k"
                    " AND (CAST(:tid AS text) IS NULL OR organization_id = :tid)"
                    " ORDER BY created_at DESC LIMIT :lim"
                ),
                {"k": kind, "tid": tenant_id, "lim": limit},
            )
            return [_row_to_doc(r) for r in rows]

    async def delete_doc(self, kind: str, doc_id: str) -> bool:
        async with tenant_tx(self._engine, None) as conn:
            res = await conn.execute(
                text("DELETE FROM tenant_documents WHERE kind = :k AND id = :id"),
                {"k": kind, "id": doc_id},
            )
        return bool(res.rowcount)

    async def assign_number(
        self, tenant_id: str, company_id: str, e164: str, assistant_id: str
    ) -> None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO phone_numbers (e164, organization_id, company_id, assistant_id,
                        carrier)
                    VALUES (:n, :oid, :cid, :aid, 'byo_sip')
                    ON CONFLICT (e164) DO UPDATE SET
                        assistant_id = EXCLUDED.assistant_id,
                        organization_id = EXCLUDED.organization_id,
                        company_id = EXCLUDED.company_id,
                        carrier = EXCLUDED.carrier
                    """
                ),
                {"n": e164, "oid": tenant_id, "cid": company_id, "aid": assistant_id},
            )
        if self._redis is not None:
            try:
                await self._redis.delete(f"parlio:assistant_config:{e164}")
            except Exception:
                log.warning("redis invalidate failed", exc_info=True)

    async def unassign_number(self, e164: str) -> None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(
                text("DELETE FROM phone_numbers WHERE e164 = :n AND carrier = 'byo_sip'"),
                {"n": e164},
            )
        if self._redis is not None:
            try:
                await self._redis.delete(f"parlio:assistant_config:{e164}")
            except Exception:
                log.warning("redis invalidate failed", exc_info=True)

    async def ensure_partitions(self) -> None:
        async with tenant_tx(self._engine, None) as conn:
            await conn.execute(text("SELECT ensure_month_partitions(3)"))
