"""Call screening & spam filtering (Phase 20d).

The worker asks `ScreeningService.assess()` at call start, before the assistant says a word:

- ``reject``  - withheld caller ID (when the tenant blocks those), a number on the tenant's or the
                platform's spam list, a contact marked *blocked*, or a number that keeps making very
                short calls (robocaller pattern). The worker hangs up without starting a session,
                so the call costs no minutes.
- ``screen``  - an unknown caller (or everyone, in strict mode): the assistant asks who is calling
                and why before helping, and ends sales/robocalls with the ``end_call`` tool.
- ``allow``   - known contacts, numbers on the allow list, or screening switched off.

Spam lists are plain tenant docs; reports made from the Calls page land on the tenant list, and
platform staff can promote numbers to the shared list that protects every tenant.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from parlio_api.store import CallRecord, CallStore, Contact, TenantDoc
from parlio_voice.models import AssistantConfig, ScreeningMode

SPAM_KIND = "spam_number"
PLATFORM_TENANT = "parlio-platform"
WITHHELD = {"", "unknown", "anonymous", "restricted", "withheld", "private", "unavailable"}

# A robocaller: this many calls inside the window, every one hung up before it went anywhere.
REPEAT_WINDOW = timedelta(hours=24)
REPEAT_MIN_CALLS = 3
REPEAT_MAX_DURATION_S = 8


class ScreeningAction(StrEnum):
    ALLOW = "allow"
    SCREEN = "screen"
    REJECT = "reject"


class ScreeningVerdict(BaseModel):
    action: ScreeningAction
    reason: str
    known_name: str | None = None
    contact_status: str | None = None


class SpamNumber(BaseModel):
    tenant_id: str
    e164: str
    reason: str = "reported"
    reported_by: str | None = None
    call_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def id(self) -> str:
        return f"{self.tenant_id}:{digits(self.e164)}"

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=SPAM_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


def digits(number: str | None) -> str:
    return re.sub(r"\D", "", number or "")


def is_withheld(number: str | None) -> bool:
    return (number or "").strip().lower() in WITHHELD or not digits(number)


def same_number(a: str | None, b: str | None) -> bool:
    da, db = digits(a), digits(b)
    return bool(da) and (da == db or da.endswith(db[-10:]) or db.endswith(da[-10:]))


def looks_like_robocaller(number: str, calls: list[CallRecord], now: datetime) -> bool:
    since = now - REPEAT_WINDOW
    recent = [
        c
        for c in calls
        if c.direction != "outbound" and c.started_at >= since and same_number(c.caller, number)
    ]
    if len(recent) < REPEAT_MIN_CALLS:
        return False
    return all(
        (c.duration_s or 0) <= REPEAT_MAX_DURATION_S and not c.ticket_ids and not c.transfers
        for c in recent
    )


class ScreeningService:
    def __init__(self, store: CallStore) -> None:
        self.store = store

    # spam lists
    async def spam_list(self, tenant_id: str) -> list[SpamNumber]:
        docs = await self.store.list_docs(SPAM_KIND, tenant_id, 500)
        return sorted((SpamNumber.model_validate(d.data) for d in docs), key=lambda s: s.created_at)

    async def report_spam(
        self,
        tenant_id: str,
        e164: str,
        *,
        reason: str = "reported",
        by: str | None = None,
        call_id: str | None = None,
    ) -> SpamNumber:
        if not digits(e164):
            raise ValueError("a phone number is required")
        s = SpamNumber(
            tenant_id=tenant_id, e164=e164, reason=reason, reported_by=by, call_id=call_id
        )
        await self.store.put_doc(s.to_doc())
        return s

    async def forgive(self, tenant_id: str, e164: str) -> bool:
        return await self.store.delete_doc(SPAM_KIND, f"{tenant_id}:{digits(e164)}")

    async def is_spam(self, tenant_id: str, number: str) -> str | None:
        for scope in (tenant_id, PLATFORM_TENANT):
            d = await self.store.get_doc(SPAM_KIND, f"{scope}:{digits(number)}")
            if d is not None:
                return "tenant spam list" if scope == tenant_id else "platform spam list"
        return None

    async def _contact(self, tenant_id: str, number: str) -> Contact | None:
        for c in await self.store.list_contacts(tenant_id, q=digits(number)[-9:], limit=20):
            if same_number(c.e164, number):
                return c
        return None

    # the decision
    async def assess(
        self, cfg: AssistantConfig, caller: str | None, now: datetime | None = None
    ) -> ScreeningVerdict:
        sc = cfg.screening
        now = now or datetime.now(UTC)
        if cfg.is_blocked(caller):
            return ScreeningVerdict(action=ScreeningAction.REJECT, reason="blocked number")
        if is_withheld(caller):
            if sc.block_withheld:
                return ScreeningVerdict(action=ScreeningAction.REJECT, reason="withheld number")
            if sc.mode != ScreeningMode.OFF:
                return ScreeningVerdict(action=ScreeningAction.SCREEN, reason="withheld number")
            return ScreeningVerdict(action=ScreeningAction.ALLOW, reason="screening off")
        assert caller is not None
        if any(same_number(caller, n) for n in sc.allow_numbers):
            return ScreeningVerdict(action=ScreeningAction.ALLOW, reason="allow list")
        contact = await self._contact(cfg.tenant_id, caller)
        if contact is not None and contact.status == "blocked":
            return ScreeningVerdict(
                action=ScreeningAction.REJECT, reason="contact blocked", known_name=contact.name
            )
        if sc.block_spam:
            listed = await self.is_spam(cfg.tenant_id, caller)
            if listed:
                return ScreeningVerdict(action=ScreeningAction.REJECT, reason=listed)
            calls = await self.store.list_calls(cfg.tenant_id, limit=300)
            if looks_like_robocaller(caller, calls, now):
                return ScreeningVerdict(
                    action=ScreeningAction.REJECT, reason="repeated short calls (robocaller)"
                )
        known = contact is not None and (
            contact.status in ("customer", "vip") or contact.vip or bool(contact.name)
        )
        if sc.mode == ScreeningMode.ALL:
            return ScreeningVerdict(
                action=ScreeningAction.SCREEN,
                reason="screen every caller",
                known_name=contact.name if contact else None,
                contact_status=contact.status if contact else None,
            )
        if sc.mode == ScreeningMode.UNKNOWN and not known:
            return ScreeningVerdict(action=ScreeningAction.SCREEN, reason="unknown caller")
        return ScreeningVerdict(
            action=ScreeningAction.ALLOW,
            reason="known contact" if known else "screening off",
            known_name=contact.name if contact else None,
            contact_status=contact.status if contact else None,
        )
