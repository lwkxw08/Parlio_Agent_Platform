"""Storage interface plus the in-memory implementation (mirrored to Redis when available).

`PostgresStore` (parlio_api.db.postgres) is the production implementation; both satisfy
`CallStore` so routes and the event consumer never care which one is active.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field, computed_field
from redis.asyncio import Redis

from parlio_voice.models import (
    AssistantConfig,
    CallEvent,
    CallEventType,
    TicketIntake,
    TicketPriority,
)

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
    # human hand-off
    transfers: list[dict[str, Any]] = Field(default_factory=list)
    ticket_ids: list[str] = Field(default_factory=list)
    escalated: bool = False
    escalation_keyword: str | None = None
    # dashboard state
    read: bool = False
    feedback: list[dict[str, Any]] = Field(default_factory=list)
    share_token: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kind(self) -> str:
        """Dashboard bucket: blocked | missed | transferred | ticketed | answered | active."""
        if self.end_reason == "blocked":
            return "blocked"
        if self.status == "failed" or (self.status == "completed" and self.answered_at is None):
            return "missed"
        if any(t.get("outcome") == "answered" for t in self.transfers):
            return "transferred"
        if self.ticket_ids:
            return "ticketed"
        return "answered" if self.status == "completed" else "active"


class CallFilter(BaseModel):
    tenant_id: str | None = None
    kind: str | None = None  # answered|missed|transferred|ticketed|blocked|escalated|unread
    since: datetime | None = None
    until: datetime | None = None
    hour: int | None = None  # 0-23, local to the assistant timezone is a later refinement
    q: str | None = None  # caller / summary substring
    limit: int = 100

    def matches(self, c: CallRecord) -> bool:
        if self.tenant_id and c.tenant_id != self.tenant_id:
            return False
        if self.since and c.started_at < self.since:
            return False
        if self.until and c.started_at >= self.until:
            return False
        if self.hour is not None and c.started_at.hour != self.hour:
            return False
        if self.kind == "escalated":
            if not c.escalated:
                return False
        elif self.kind == "unread":
            if c.read:
                return False
        elif self.kind and c.kind != self.kind:
            return False
        if self.q:
            hay = " ".join(filter(None, [c.caller, c.summary, c.dialed])).lower()
            if self.q.lower() not in hay:
                return False
        return True


class CallFeedback(BaseModel):
    type: str  # incorrect_response | tone | missed_transfer | latency | other
    note: str | None = None
    actor: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Contact(BaseModel):
    id: str
    tenant_id: str
    company_id: str
    e164: str
    name: str | None = None
    email: str | None = None
    vip: bool = False
    notes: str | None = None
    status: str = "prospect"  # prospect | customer | blocked
    call_count: int = 0
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContactUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    vip: bool | None = None
    notes: str | None = None
    status: str | None = None


class CallRedaction(BaseModel):
    """Fields overwritten in place by retention / PII redaction; None = leave unchanged."""

    caller: str | None = None
    transcript: list[dict[str, Any]] | None = None
    summary: str | None = None
    extracted: dict[str, Any] | None = None
    recordings: list[str] | None = None


class Member(BaseModel):
    tenant_id: str
    user_id: str
    email: str
    name: str | None = None
    role: str = "member"  # owner | admin | member | viewer
    status: str = "active"  # active | invited
    invited_at: datetime | None = None


class AssistantVersion(BaseModel):
    assistant_id: str
    version: int
    created_at: datetime
    created_by: str | None = None
    note: str | None = None
    config: AssistantConfig


class TransferRecord(BaseModel):
    id: str
    tenant_id: str
    call_id: str
    destination: str
    destination_id: str | None = None
    department: str | None = None
    mode: str = "warm"
    outcome: str
    reason: str | None = None
    started_at: datetime
    ended_at: datetime | None = None


class TicketStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class Ticket(BaseModel):
    id: str
    tenant_id: str
    company_id: str
    call_id: str | None = None
    contact_id: str | None = None
    status: TicketStatus = TicketStatus.OPEN
    priority: TicketPriority = TicketPriority.NORMAL
    category: str | None = None
    department: str | None = None
    caller_name: str | None = None
    caller_number: str | None = None
    reason: str
    callback_window: str | None = None
    source: str = "ai_intake"
    sla_due_at: datetime | None = None
    sla_breached: bool = False
    assigned_to: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None

    @property
    def sla_remaining_s(self) -> float | None:
        if self.sla_due_at is None or self.status in (
            TicketStatus.RESOLVED,
            TicketStatus.CANCELLED,
        ):
            return None
        return (self.sla_due_at - datetime.now(UTC)).total_seconds()


class TicketEvent(BaseModel):
    ticket_id: str
    type: str  # created | claimed | assigned | note | resolved | reopened | sla_breached | callback
    actor: str | None = None
    note: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TicketUpdate(BaseModel):
    status: TicketStatus | None = None
    assigned_to: str | None = None
    priority: TicketPriority | None = None
    note: str | None = None
    actor: str | None = None


class TransferStats(BaseModel):
    total: int = 0
    by_outcome: dict[str, int] = Field(default_factory=dict)
    by_department: dict[str, int] = Field(default_factory=dict)
    by_destination: dict[str, int] = Field(default_factory=dict)
    answer_rate: float | None = None


class TicketStats(BaseModel):
    total: int = 0
    open: int = 0
    claimed: int = 0
    resolved: int = 0
    by_priority: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    sla_breached: int = 0
    avg_time_to_claim_s: float | None = None
    avg_time_to_resolve_s: float | None = None


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
    async def upsert_assistant(self, cfg: AssistantConfig, numbers: list[str]) -> None:
        """Save as a new version; `cfg.assistant_version` is set to the stored version."""
        ...

    async def get_assistant(self, assistant_id: str) -> AssistantConfig | None: ...
    async def list_assistant_versions(self, assistant_id: str) -> list[AssistantVersion]: ...
    async def get_assistant_version(
        self, assistant_id: str, version: int
    ) -> AssistantConfig | None: ...
    async def resolve_number(self, number: str) -> AssistantConfig | None: ...
    async def list_assistants(self, tenant_id: str | None = None) -> list[AssistantConfig]: ...
    async def list_calls(
        self, tenant_id: str | None = None, limit: int = 50
    ) -> list[CallRecord]: ...
    async def filter_calls(self, f: CallFilter) -> list[CallRecord]: ...
    async def get_call(self, call_id: str) -> CallRecord | None: ...
    async def get_call_by_share_token(self, token: str) -> CallRecord | None: ...
    async def mark_call_read(self, call_id: str, read: bool = True) -> CallRecord | None: ...
    async def add_call_feedback(self, call_id: str, fb: CallFeedback) -> CallRecord | None: ...
    async def ensure_share_token(self, call_id: str) -> str | None: ...
    async def apply_event(self, ev: CallEvent) -> bool: ...
    async def record_postcall(self, call_id: str, result: PostCallResult) -> None: ...
    async def touch_contact(self, tenant_id: str, company_id: str, e164: str) -> tuple[str, bool]:
        """Upsert a contact by number; returns (contact_id, is_returning)."""
        ...

    async def list_contacts(
        self, tenant_id: str | None = None, q: str | None = None, limit: int = 200
    ) -> list[Contact]: ...
    async def get_contact(self, contact_id: str) -> Contact | None: ...
    async def update_contact(self, contact_id: str, upd: ContactUpdate) -> Contact | None: ...
    async def required_fields(self, assistant_id: str) -> list[RequiredField]: ...
    async def set_required_fields(self, assistant_id: str, fields: list[RequiredField]) -> None: ...

    # organisation members (Phase 4)
    async def list_members(self, tenant_id: str) -> list[Member]: ...
    async def upsert_member(self, m: Member) -> Member: ...
    async def remove_member(self, tenant_id: str, user_id: str) -> bool: ...
    async def memberships_for_email(self, email: str) -> list[Member]: ...
    async def create_worker_key(self, tenant_id: str | None, name: str) -> str: ...
    async def verify_worker_key(self, key: str) -> bool: ...

    # transfers / tickets (Phase 3)
    async def list_transfers(
        self, tenant_id: str | None = None, limit: int = 100
    ) -> list[TransferRecord]: ...
    async def transfer_stats(self, tenant_id: str | None = None) -> TransferStats: ...
    async def create_ticket(self, ticket: Ticket) -> Ticket: ...
    async def get_ticket(self, ticket_id: str) -> Ticket | None: ...
    async def list_tickets(
        self,
        tenant_id: str | None = None,
        status: TicketStatus | None = None,
        limit: int = 100,
    ) -> list[Ticket]: ...
    async def update_ticket(self, ticket_id: str, upd: TicketUpdate) -> Ticket | None: ...
    async def add_ticket_event(self, ev: TicketEvent) -> None: ...
    async def ticket_events(self, ticket_id: str) -> list[TicketEvent]: ...
    async def overdue_tickets(self, now: datetime) -> list[Ticket]:
        """Open/claimed tickets past `sla_due_at` not yet flagged as breached."""
        ...

    async def mark_sla_breached(self, ticket_id: str) -> None: ...
    async def ticket_stats(self, tenant_id: str | None = None) -> TicketStats: ...

    # -- tenant documents (Phase 5 integrations: messages, notification rules, trunks, ...) ---
    async def put_doc(self, doc: TenantDoc) -> TenantDoc: ...
    async def get_doc(self, kind: str, doc_id: str) -> TenantDoc | None: ...
    async def list_docs(
        self, kind: str, tenant_id: str | None = None, limit: int = 200
    ) -> list[TenantDoc]: ...
    async def delete_doc(self, kind: str, doc_id: str) -> bool: ...

    # -- compliance (Phase 6) ---
    async def redact_call(self, call_id: str, r: CallRedaction) -> CallRecord | None: ...
    async def purge_calls(
        self, tenant_id: str, before: datetime | None = None, call_ids: list[str] | None = None
    ) -> int:
        """Delete calls (+ transcripts/recordings refs) for a tenant; returns rows removed."""
        ...

    async def delete_contact(self, tenant_id: str, contact_id: str) -> bool: ...
    async def anonymise_tickets(self, tenant_id: str, ticket_ids: list[str]) -> int:
        """Blank caller name/number on the given tickets (GDPR erasure); returns rows touched."""
        ...

    async def assign_number(
        self, tenant_id: str, company_id: str, e164: str, assistant_id: str
    ) -> None:
        """Route an inbound number (DDI) to an assistant; used by BYO SIP trunk DDIs."""
        ...

    async def unassign_number(self, e164: str) -> None: ...


class TenantDoc(BaseModel):
    """Schemaless tenant-owned record; typed models live in the owning service module."""

    kind: str
    id: str
    tenant_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def transfer_from_event(ev: CallEvent) -> TransferRecord | None:
    """Build a TransferRecord from a `call.transfer_completed` event (None if no attempt made)."""
    p = ev.payload
    if ev.type != CallEventType.TRANSFER_COMPLETED or not p.get("transfer_id"):
        return None
    started = p.get("started_at")
    ended = p.get("ended_at")
    return TransferRecord(
        id=str(p["transfer_id"]),
        tenant_id=ev.tenant_id,
        call_id=ev.call_id,
        destination=str(p.get("destination", "")),
        destination_id=p.get("destination_id"),
        department=p.get("department"),
        mode=str(p.get("mode", "warm")),
        outcome=str(p.get("outcome", "no_answer")),
        reason=p.get("reason"),
        started_at=datetime.fromisoformat(started) if started else ev.occurred_at,
        ended_at=datetime.fromisoformat(ended) if ended else ev.occurred_at,
    )


def intake_from_event(ev: CallEvent) -> TicketIntake | None:
    """Intake carried on a `call.ticket_created` event whose API call failed (no ticket_id)."""
    if ev.type != CallEventType.TICKET_CREATED or ev.payload.get("ticket_id"):
        return None
    raw = ev.payload.get("intake")
    return TicketIntake.model_validate(raw) if raw else None


def apply_ticket_update(t: Ticket, upd: TicketUpdate, now: datetime) -> list[TicketEvent]:
    """Mutate `t` per `upd` and return the audit events to append (shared by both stores)."""
    events: list[TicketEvent] = []
    if upd.assigned_to is not None and upd.assigned_to != t.assigned_to:
        t.assigned_to = upd.assigned_to
        events.append(
            TicketEvent(
                ticket_id=t.id, type="assigned", actor=upd.actor, note=upd.assigned_to, at=now
            )
        )
        if t.status == TicketStatus.OPEN and upd.status is None:
            upd = upd.model_copy(update={"status": TicketStatus.CLAIMED})
    if upd.priority is not None and upd.priority != t.priority:
        t.priority = upd.priority
        events.append(
            TicketEvent(ticket_id=t.id, type="priority", actor=upd.actor, note=upd.priority, at=now)
        )
    if upd.status is not None and upd.status != t.status:
        prev = t.status
        t.status = upd.status
        kind = {
            TicketStatus.CLAIMED: "claimed",
            TicketStatus.RESOLVED: "resolved",
            TicketStatus.CANCELLED: "cancelled",
            TicketStatus.OPEN: "reopened",
        }[upd.status]
        if upd.status == TicketStatus.CLAIMED and upd.actor and not t.assigned_to:
            t.assigned_to = upd.actor
        if upd.status in (TicketStatus.RESOLVED, TicketStatus.CANCELLED):
            t.resolved_at = now
        elif prev in (TicketStatus.RESOLVED, TicketStatus.CANCELLED):
            t.resolved_at = None
        events.append(TicketEvent(ticket_id=t.id, type=kind, actor=upd.actor, at=now))
    if upd.note:
        events.append(
            TicketEvent(ticket_id=t.id, type="note", actor=upd.actor, note=upd.note, at=now)
        )
    if events:
        t.updated_at = now
    return events


def compute_transfer_stats(rows: list[TransferRecord]) -> TransferStats:
    s = TransferStats(total=len(rows))
    for r in rows:
        s.by_outcome[r.outcome] = s.by_outcome.get(r.outcome, 0) + 1
        dep = r.department or "general"
        s.by_department[dep] = s.by_department.get(dep, 0) + 1
        s.by_destination[r.destination] = s.by_destination.get(r.destination, 0) + 1
    if rows:
        s.answer_rate = round(s.by_outcome.get("answered", 0) / len(rows), 3)
    return s


def compute_ticket_stats(tickets: list[Ticket], events: list[TicketEvent]) -> TicketStats:
    s = TicketStats(total=len(tickets))
    claimed_at: dict[str, datetime] = {}
    for e in events:
        if e.type == "claimed" and e.ticket_id not in claimed_at:
            claimed_at[e.ticket_id] = e.at
    claim_deltas: list[float] = []
    resolve_deltas: list[float] = []
    for t in tickets:
        if t.status == TicketStatus.OPEN:
            s.open += 1
        elif t.status == TicketStatus.CLAIMED:
            s.claimed += 1
        elif t.status == TicketStatus.RESOLVED:
            s.resolved += 1
        s.by_priority[t.priority] = s.by_priority.get(t.priority, 0) + 1
        cat = t.category or "uncategorised"
        s.by_category[cat] = s.by_category.get(cat, 0) + 1
        if t.sla_breached:
            s.sla_breached += 1
        if t.id in claimed_at:
            claim_deltas.append((claimed_at[t.id] - t.created_at).total_seconds())
        if t.resolved_at and t.status == TicketStatus.RESOLVED:
            resolve_deltas.append((t.resolved_at - t.created_at).total_seconds())
    if claim_deltas:
        s.avg_time_to_claim_s = round(sum(claim_deltas) / len(claim_deltas), 1)
    if resolve_deltas:
        s.avg_time_to_resolve_s = round(sum(resolve_deltas) / len(resolve_deltas), 1)
    return s


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
        case CallEventType.SUPERVISOR:
            call.transcript.append(
                {
                    "role": "supervisor",
                    "text": p.get("text") or f"[{p.get('cmd')} by {p.get('by')}]",
                    "at": ev.occurred_at.isoformat(),
                }
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
        case CallEventType.TRANSFER_COMPLETED:
            if p.get("transfer_id"):
                call.transfers.append(
                    {
                        "transfer_id": p["transfer_id"],
                        "destination": p.get("destination"),
                        "department": p.get("department"),
                        "mode": p.get("mode"),
                        "outcome": p.get("outcome"),
                        "at": ev.occurred_at.isoformat(),
                    }
                )
        case CallEventType.TICKET_CREATED:
            tid = p.get("ticket_id")
            if tid and tid not in call.ticket_ids:
                call.ticket_ids.append(tid)
        case CallEventType.ESCALATION:
            call.escalated = True
            call.escalation_keyword = p.get("keyword")
        case CallEventType.TURN_COMPLETED | CallEventType.TRANSFER_STARTED:
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
        self._contacts: dict[str, Contact] = {}
        self._contact_by_number: dict[tuple[str, str], str] = {}
        self._versions: dict[str, list[AssistantVersion]] = {}
        self._members: dict[tuple[str, str], Member] = {}
        self._required: dict[str, list[RequiredField]] = {}
        self._worker_keys: set[str] = set()
        self._transfers: dict[str, TransferRecord] = {}
        self._tickets: dict[str, Ticket] = {}
        self._ticket_events: list[TicketEvent] = []
        self._docs: dict[tuple[str, str], TenantDoc] = {}

    # -- assistants ---------------------------------------------------------------------------
    async def upsert_assistant(self, cfg: AssistantConfig, numbers: list[str]) -> None:
        prev = self._assistants.get(cfg.assistant_id)
        cfg.assistant_version = (prev.assistant_version + 1) if prev else 1
        self._assistants[cfg.assistant_id] = cfg
        self._versions.setdefault(cfg.assistant_id, []).append(
            AssistantVersion(
                assistant_id=cfg.assistant_id,
                version=cfg.assistant_version,
                created_at=datetime.now(UTC),
                config=cfg.model_copy(deep=True),
            )
        )
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

    async def list_assistant_versions(self, assistant_id: str) -> list[AssistantVersion]:
        return sorted(self._versions.get(assistant_id, []), key=lambda v: -v.version)

    async def get_assistant_version(
        self, assistant_id: str, version: int
    ) -> AssistantConfig | None:
        for v in self._versions.get(assistant_id, []):
            if v.version == version:
                return v.config.model_copy(deep=True)
        return None

    # -- calls --------------------------------------------------------------------------------
    async def list_calls(self, tenant_id: str | None = None, limit: int = 50) -> list[CallRecord]:
        calls = [c for c in self._calls.values() if tenant_id is None or c.tenant_id == tenant_id]
        calls.sort(key=lambda c: c.started_at, reverse=True)
        return calls[:limit]

    async def filter_calls(self, f: CallFilter) -> list[CallRecord]:
        calls = [c for c in self._calls.values() if f.matches(c)]
        calls.sort(key=lambda c: c.started_at, reverse=True)
        return calls[: f.limit]

    async def get_call(self, call_id: str) -> CallRecord | None:
        return self._calls.get(call_id)

    async def get_call_by_share_token(self, token: str) -> CallRecord | None:
        return next((c for c in self._calls.values() if c.share_token == token), None)

    async def mark_call_read(self, call_id: str, read: bool = True) -> CallRecord | None:
        call = self._calls.get(call_id)
        if call is not None:
            call.read = read
        return call

    async def add_call_feedback(self, call_id: str, fb: CallFeedback) -> CallRecord | None:
        call = self._calls.get(call_id)
        if call is not None:
            call.feedback.append(fb.model_dump(mode="json"))
        return call

    async def ensure_share_token(self, call_id: str) -> str | None:
        call = self._calls.get(call_id)
        if call is None:
            return None
        if not call.share_token:
            call.share_token = secrets.token_urlsafe(16)
        return call.share_token

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
        tr = transfer_from_event(ev)
        if tr is not None:
            self._transfers[tr.id] = tr
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
        cid = self._contact_by_number.get(key)
        if cid is None:
            cid = f"contact-{len(self._contacts) + 1}"
            self._contact_by_number[key] = cid
            self._contacts[cid] = Contact(
                id=cid, tenant_id=tenant_id, company_id=company_id, e164=e164, call_count=1
            )
            return cid, False
        c = self._contacts[cid]
        c.call_count += 1
        c.last_seen_at = datetime.now(UTC)
        return cid, True

    async def list_contacts(
        self, tenant_id: str | None = None, q: str | None = None, limit: int = 200
    ) -> list[Contact]:
        out = [
            c
            for c in self._contacts.values()
            if (tenant_id is None or c.tenant_id == tenant_id)
            and (not q or q.lower() in f"{c.e164} {c.name or ''} {c.email or ''}".lower())
        ]
        out.sort(key=lambda c: c.last_seen_at, reverse=True)
        return out[:limit]

    async def get_contact(self, contact_id: str) -> Contact | None:
        return self._contacts.get(contact_id)

    async def update_contact(self, contact_id: str, upd: ContactUpdate) -> Contact | None:
        c = self._contacts.get(contact_id)
        if c is None:
            return None
        for k, v in upd.model_dump(exclude_none=True).items():
            setattr(c, k, v)
        return c

    # -- members ------------------------------------------------------------------------------
    async def list_members(self, tenant_id: str) -> list[Member]:
        return sorted(
            (m for m in self._members.values() if m.tenant_id == tenant_id), key=lambda m: m.email
        )

    async def upsert_member(self, m: Member) -> Member:
        self._members[(m.tenant_id, m.user_id)] = m
        return m

    async def remove_member(self, tenant_id: str, user_id: str) -> bool:
        return self._members.pop((tenant_id, user_id), None) is not None

    async def memberships_for_email(self, email: str) -> list[Member]:
        return [m for m in self._members.values() if m.email.lower() == email.lower()]

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

    # -- transfers / tickets ------------------------------------------------------------------
    async def list_transfers(
        self, tenant_id: str | None = None, limit: int = 100
    ) -> list[TransferRecord]:
        rows = [
            t for t in self._transfers.values() if tenant_id is None or t.tenant_id == tenant_id
        ]
        rows.sort(key=lambda t: t.started_at, reverse=True)
        return rows[:limit]

    async def transfer_stats(self, tenant_id: str | None = None) -> TransferStats:
        return compute_transfer_stats(await self.list_transfers(tenant_id, limit=10_000))

    async def create_ticket(self, ticket: Ticket) -> Ticket:
        self._tickets[ticket.id] = ticket
        self._ticket_events.append(
            TicketEvent(
                ticket_id=ticket.id, type="created", note=ticket.source, at=ticket.created_at
            )
        )
        call = self._calls.get(ticket.call_id or "")
        if call is not None and ticket.id not in call.ticket_ids:
            call.ticket_ids.append(ticket.id)
        return ticket

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        return self._tickets.get(ticket_id)

    async def list_tickets(
        self,
        tenant_id: str | None = None,
        status: TicketStatus | None = None,
        limit: int = 100,
    ) -> list[Ticket]:
        rows = [
            t
            for t in self._tickets.values()
            if (tenant_id is None or t.tenant_id == tenant_id)
            and (status is None or t.status == status)
        ]
        rows.sort(key=lambda t: t.created_at, reverse=True)
        return rows[:limit]

    async def update_ticket(self, ticket_id: str, upd: TicketUpdate) -> Ticket | None:
        t = self._tickets.get(ticket_id)
        if t is None:
            return None
        self._ticket_events.extend(apply_ticket_update(t, upd, datetime.now(UTC)))
        return t

    async def add_ticket_event(self, ev: TicketEvent) -> None:
        self._ticket_events.append(ev)

    async def ticket_events(self, ticket_id: str) -> list[TicketEvent]:
        return [e for e in self._ticket_events if e.ticket_id == ticket_id]

    async def overdue_tickets(self, now: datetime) -> list[Ticket]:
        return [
            t
            for t in self._tickets.values()
            if t.status in (TicketStatus.OPEN, TicketStatus.CLAIMED)
            and not t.sla_breached
            and t.sla_due_at is not None
            and t.sla_due_at <= now
        ]

    async def mark_sla_breached(self, ticket_id: str) -> None:
        t = self._tickets.get(ticket_id)
        if t is not None:
            t.sla_breached = True
            t.updated_at = datetime.now(UTC)

    async def ticket_stats(self, tenant_id: str | None = None) -> TicketStats:
        tickets = await self.list_tickets(tenant_id, limit=10_000)
        ids = {t.id for t in tickets}
        return compute_ticket_stats(tickets, [e for e in self._ticket_events if e.ticket_id in ids])

    # -- tenant documents ---------------------------------------------------------------------
    async def put_doc(self, doc: TenantDoc) -> TenantDoc:
        key = (doc.kind, doc.id)
        prev = self._docs.get(key)
        doc = doc.model_copy(
            update={
                "created_at": prev.created_at if prev else doc.created_at,
                "updated_at": datetime.now(UTC),
            }
        )
        self._docs[key] = doc
        return doc

    async def get_doc(self, kind: str, doc_id: str) -> TenantDoc | None:
        return self._docs.get((kind, doc_id))

    async def list_docs(
        self, kind: str, tenant_id: str | None = None, limit: int = 200
    ) -> list[TenantDoc]:
        docs = [
            d
            for d in self._docs.values()
            if d.kind == kind and (tenant_id is None or d.tenant_id == tenant_id)
        ]
        docs.sort(key=lambda d: d.created_at, reverse=True)
        return docs[:limit]

    async def delete_doc(self, kind: str, doc_id: str) -> bool:
        return self._docs.pop((kind, doc_id), None) is not None

    async def redact_call(self, call_id: str, r: CallRedaction) -> CallRecord | None:
        call = self._calls.get(call_id)
        if call is None:
            return None
        updated = call.model_copy(update=r.model_dump(exclude_none=True))
        self._calls[call_id] = updated
        return updated

    async def purge_calls(
        self, tenant_id: str, before: datetime | None = None, call_ids: list[str] | None = None
    ) -> int:
        victims = [
            cid
            for cid, c in self._calls.items()
            if c.tenant_id == tenant_id
            and (before is None or c.started_at < before)
            and (call_ids is None or cid in call_ids)
        ]
        for cid in victims:
            del self._calls[cid]
        return len(victims)

    async def delete_contact(self, tenant_id: str, contact_id: str) -> bool:
        c = self._contacts.get(contact_id)
        if c is None or c.tenant_id != tenant_id:
            return False
        del self._contacts[contact_id]
        for call in self._calls.values():
            if call.contact_id == contact_id:
                self._calls[call.call_id] = call.model_copy(update={"contact_id": None})
        return True

    async def anonymise_tickets(self, tenant_id: str, ticket_ids: list[str]) -> int:
        n = 0
        for tid in ticket_ids:
            t = self._tickets.get(tid)
            if t is not None and t.tenant_id == tenant_id:
                self._tickets[tid] = t.model_copy(
                    update={"caller_name": "[erased]", "caller_number": None, "contact_id": None}
                )
                n += 1
        return n

    async def assign_number(
        self, tenant_id: str, company_id: str, e164: str, assistant_id: str
    ) -> None:
        self._number_to_assistant[e164] = assistant_id
        if self._redis is not None:
            try:
                await self._redis.set(f"parlio:number:{e164}", assistant_id)
                await self._redis.delete(f"parlio:assistant_config:{e164}")
            except Exception:
                log.warning("redis mirror failed", exc_info=True)

    async def unassign_number(self, e164: str) -> None:
        self._number_to_assistant.pop(e164, None)
        if self._redis is not None:
            try:
                await self._redis.delete(f"parlio:number:{e164}", f"parlio:assistant_config:{e164}")
            except Exception:
                log.warning("redis mirror failed", exc_info=True)
