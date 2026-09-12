"""Phase 6 compliance: per-tenant retention policy, PII redaction, GDPR export / erasure.

Retention runs as a background sweep (like the SLA monitor): transcripts/recordings are redacted
after `transcript_days`, and whole call rows are purged after `call_days`. Redaction is
pattern-based (UK phone, email, card/PAN, UK postcode, NI number, sort code/account) and applied
to transcripts, summaries and extracted fields; it can also be applied at write time via
`redact_on_write` so raw PII never lands in the store.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from parlio_api.store import (
    CallFilter,
    CallRecord,
    CallRedaction,
    CallStore,
    Contact,
    TenantDoc,
    Ticket,
)

log = logging.getLogger("parlio.compliance")


class RetentionPolicy(BaseModel):
    tenant_id: str
    transcript_days: int = 90  # redact transcript + summary + extracted after N days
    recording_days: int = 30  # drop recording references after N days
    call_days: int = 365  # purge call rows entirely after N days
    redact_on_write: bool = False  # scrub PII from transcripts as they arrive
    redact_caller_number: bool = False  # mask caller E.164 when transcripts are redacted
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @staticmethod
    def default(tenant_id: str) -> RetentionPolicy:
        return RetentionPolicy(tenant_id=tenant_id)


# -- PII redaction -------------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("phone", re.compile(r"(?:\+44\s?|\b0)(?:\d\s?){9,10}\b")),
    ("ni", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.I)),
    ("sortcode", re.compile(r"\b\d{2}-\d{2}-\d{2}\b")),
    ("postcode", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.I)),
]

_SPOKEN_DIGITS = re.compile(
    r"\b(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)\b[\s,-]*){7,}", re.I
)


def redact_text(text: str) -> str:
    out = text
    for label, pat in _PATTERNS:
        out = pat.sub(f"[{label}]", out)
    return _SPOKEN_DIGITS.sub("[digits] ", out)


def redact_value(v: Any) -> Any:
    if isinstance(v, str):
        return redact_text(v)
    if isinstance(v, list):
        return [redact_value(x) for x in v]
    if isinstance(v, dict):
        return {k: redact_value(x) for k, x in v.items()}
    return v


def redact_transcript(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**it, "text": redact_text(str(it.get("text", "")))} for it in items]


def mask_e164(e164: str | None) -> str | None:
    if not e164:
        return e164
    return e164[:-6] + "******" if len(e164) > 6 else "******"


# -- GDPR export ----------------------------------------------------------------------------------


class SubjectExport(BaseModel):
    """Everything the tenant holds about one caller (data-subject access request)."""

    tenant_id: str
    subject_e164: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    contact: Contact | None = None
    calls: list[CallRecord] = Field(default_factory=list)
    tickets: list[Ticket] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)


class ErasureResult(BaseModel):
    tenant_id: str
    subject_e164: str
    calls_purged: int
    tickets_anonymised: int
    messages_deleted: int
    contact_deleted: bool


class RetentionRun(BaseModel):
    tenant_id: str
    transcripts_redacted: int = 0
    recordings_dropped: int = 0
    calls_purged: int = 0
    ran_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ComplianceService:
    KIND = "retention_policy"
    RUN_KIND = "retention_run"

    def __init__(self, store: CallStore, sweep_interval_s: float = 3600.0) -> None:
        self.store = store
        self.sweep_interval_s = sweep_interval_s
        self._task: asyncio.Task[None] | None = None

    # policy
    async def policy(self, tenant_id: str) -> RetentionPolicy:
        doc = await self.store.get_doc(self.KIND, tenant_id)
        if doc is None:
            return RetentionPolicy.default(tenant_id)
        return RetentionPolicy.model_validate(doc.data)

    async def set_policy(self, policy: RetentionPolicy) -> RetentionPolicy:
        if not (1 <= policy.recording_days <= policy.transcript_days <= policy.call_days <= 3650):
            raise ValueError("expected 1 <= recording_days <= transcript_days <= call_days <= 3650")
        policy = policy.model_copy(update={"updated_at": datetime.now(UTC)})
        await self.store.put_doc(
            TenantDoc(
                kind=self.KIND,
                id=policy.tenant_id,
                tenant_id=policy.tenant_id,
                data=policy.model_dump(mode="json"),
            )
        )
        return policy

    async def redact_call_on_close(self, tenant_id: str, call_id: str) -> bool:
        """Apply the tenant's redact-on-write policy to a call as soon as it ends."""
        pol = await self.policy(tenant_id)
        if not pol.redact_on_write:
            return False
        call = await self.store.get_call(call_id)
        if call is None or call.tenant_id != tenant_id:
            return False
        r = CallRedaction(
            transcript=redact_transcript(call.transcript) if call.transcript else None,
            summary=redact_text(call.summary) if call.summary else None,
            extracted=redact_value(call.extracted) if call.extracted else None,
        )
        if pol.redact_caller_number and call.caller and "*" not in call.caller:
            r.caller = mask_e164(call.caller)
        await self.store.redact_call(call_id, r)
        return True

    # retention sweep
    async def sweep_tenant(self, tenant_id: str, now: datetime | None = None) -> RetentionRun:
        now = now or datetime.now(UTC)
        pol = await self.policy(tenant_id)
        run = RetentionRun(tenant_id=tenant_id)
        run.calls_purged = await self.store.purge_calls(
            tenant_id, before=now - timedelta(days=pol.call_days)
        )
        calls = await self.store.filter_calls(
            CallFilter(
                tenant_id=tenant_id,
                until=now - timedelta(days=min(pol.transcript_days, pol.recording_days)),
                limit=5000,
            )
        )
        for c in calls:
            age = now - c.started_at
            r = CallRedaction()
            if age > timedelta(days=pol.recording_days) and c.recordings:
                r.recordings = []
                run.recordings_dropped += 1
            if age > timedelta(days=pol.transcript_days) and (c.transcript or c.summary):
                r.transcript = []
                r.summary = "[redacted by retention policy]" if c.summary else None
                r.extracted = {}
                if pol.redact_caller_number and c.caller and "*" not in c.caller:
                    r.caller = mask_e164(c.caller)
                run.transcripts_redacted += 1
            if r.model_dump(exclude_none=True):
                await self.store.redact_call(c.call_id, r)
        await self.store.put_doc(
            TenantDoc(
                kind=self.RUN_KIND,
                id=tenant_id,
                tenant_id=tenant_id,
                data=run.model_dump(mode="json"),
            )
        )
        return run

    async def last_run(self, tenant_id: str) -> RetentionRun | None:
        doc = await self.store.get_doc(self.RUN_KIND, tenant_id)
        return RetentionRun.model_validate(doc.data) if doc else None

    async def sweep_all(self) -> list[RetentionRun]:
        tenants = {a.tenant_id for a in await self.store.list_assistants(None)}
        out = []
        for t in sorted(tenants):
            try:
                out.append(await self.sweep_tenant(t))
            except Exception:
                log.warning("retention sweep failed for %s", t, exc_info=True)
        return out

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="retention-sweep")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.sweep_interval_s)
            await self.sweep_all()

    # GDPR
    async def _subject_calls(self, tenant_id: str, e164: str) -> list[CallRecord]:
        calls = await self.store.filter_calls(CallFilter(tenant_id=tenant_id, limit=100000))
        return [c for c in calls if c.caller == e164]

    async def _subject_contact(self, tenant_id: str, e164: str) -> Contact | None:
        return next(
            (
                c
                for c in await self.store.list_contacts(tenant_id, q=e164, limit=50)
                if c.e164 == e164
            ),
            None,
        )

    async def export_subject(self, tenant_id: str, e164: str) -> SubjectExport:
        calls = await self._subject_calls(tenant_id, e164)
        call_ids = {c.call_id for c in calls}
        tickets = [
            t
            for t in await self.store.list_tickets(tenant_id, None, limit=100000)
            if t.caller_number == e164 or (t.call_id and t.call_id in call_ids)
        ]
        msgs = [
            d.data
            for d in await self.store.list_docs("message", tenant_id, limit=100000)
            if d.data.get("to") == e164
        ]
        return SubjectExport(
            tenant_id=tenant_id,
            subject_e164=e164,
            contact=await self._subject_contact(tenant_id, e164),
            calls=calls,
            tickets=tickets,
            messages=msgs,
        )

    async def erase_subject(self, tenant_id: str, e164: str) -> ErasureResult:
        calls = await self._subject_calls(tenant_id, e164)
        call_ids = [c.call_id for c in calls]
        purged = await self.store.purge_calls(tenant_id, call_ids=call_ids) if call_ids else 0
        ticket_ids = [
            t.id
            for t in await self.store.list_tickets(tenant_id, None, limit=100000)
            if t.caller_number == e164 or (t.call_id and t.call_id in call_ids)
        ]
        anonymised = await self.store.anonymise_tickets(tenant_id, ticket_ids)
        deleted_msgs = 0
        for d in await self.store.list_docs("message", tenant_id, limit=100000):
            if d.data.get("to") == e164 and await self.store.delete_doc("message", d.id):
                deleted_msgs += 1
        contact = await self._subject_contact(tenant_id, e164)
        contact_deleted = (
            await self.store.delete_contact(tenant_id, contact.id) if contact else False
        )
        return ErasureResult(
            tenant_id=tenant_id,
            subject_e164=e164,
            calls_purged=purged,
            tickets_anonymised=anonymised,
            messages_deleted=deleted_msgs,
            contact_deleted=contact_deleted,
        )
