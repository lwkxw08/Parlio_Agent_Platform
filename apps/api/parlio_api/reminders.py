"""SMS appointment reminders with reply-to-confirm / reply-to-reschedule (Phase 20b).

Each booking gets one reminder text per configured offset ("24h before"). The customer replies
``1``/``YES`` to confirm or ``2``/``CHANGE`` to reschedule: a confirmation flips the booking to
``confirmed``; a reschedule request marks it ``reschedule_requested`` and raises a callback ticket
so a person (or the AI, once the team gives it a resolution) rings them to find a new slot.
Anything else falls through to the normal inbox AI so a real question still gets a real answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from parlio_api.calendar import BOOKING_KIND, Booking
from parlio_api.messaging import Message, MessageService, MessageStatus
from parlio_api.store import CallStore, TenantDoc, Ticket
from parlio_voice.models import SmsTrigger, TicketIntake, TicketPriority

log = logging.getLogger("parlio.reminders")

REMINDER_KIND = "appointment_reminder"
POLICY_KIND = "reminder_policy"
# How long after a reminder is sent a reply is still treated as being about that booking.
REPLY_WINDOW = timedelta(days=3)

CONFIRM_WORDS = {
    "1",
    "y",
    "yes",
    "yeah",
    "yep",
    "ok",
    "okay",
    "confirm",
    "confirmed",
    "c",
    "confirm 1",
}
RESCHEDULE_WORDS = {
    "2",
    "n",
    "no",
    "change",
    "reschedule",
    "rearrange",
    "move",
    "cancel",
    "cant make it",
    "can't make it",
    "r",
}


class ReminderStatus(StrEnum):
    SCHEDULED = "scheduled"
    SENT = "sent"
    CONFIRMED = "confirmed"
    RESCHEDULE_REQUESTED = "reschedule_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ReminderPolicy(BaseModel):
    """Per-tenant reminder settings (Integrations -> Calendar)."""

    tenant_id: str
    enabled: bool = False
    timezone: str = "Europe/London"
    hours_before: list[int] = Field(default_factory=lambda: [24])
    confirmation_enabled: bool = True
    confirmation_template: str = (
        "{business}: your appointment is booked for {when}. Reply STOP to opt out of texts."
    )
    template: str = (
        "{business}: reminder of your appointment on {when}. Reply 1 to confirm or 2 to "
        "reschedule. Reply STOP to opt out."
    )
    confirm_reply: str = "Thanks {name}, you're confirmed for {when}. See you then - {business}"
    reschedule_reply: str = (
        "No problem {name}, we'll call you shortly to find a new time - {business}"
    )

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=POLICY_KIND,
            id=self.tenant_id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
        )


class AppointmentReminder(BaseModel):
    id: str = Field(default_factory=lambda: f"rem-{uuid4().hex[:10]}")
    tenant_id: str
    company_id: str
    booking_id: str
    phone: str
    name: str
    start: datetime
    send_at: datetime
    status: ReminderStatus = ReminderStatus.SCHEDULED
    sent_at: datetime | None = None
    message_id: str | None = None
    reply: str | None = None
    replied_at: datetime | None = None
    ticket_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=REMINDER_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class ReminderReply(BaseModel):
    """Outcome of interpreting an inbound SMS as a reminder reply."""

    reminder: AppointmentReminder
    booking: Booking | None
    action: ReminderStatus
    text: str


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9' ]+", " ", s.lower()).strip()


def interpret_reply(text: str) -> ReminderStatus | None:
    """``1``/``yes`` -> CONFIRMED, ``2``/``change`` -> RESCHEDULE_REQUESTED, else None."""
    t = _norm(text)
    if not t:
        return None
    if t in CONFIRM_WORDS:
        return ReminderStatus.CONFIRMED
    if t in RESCHEDULE_WORDS:
        return ReminderStatus.RESCHEDULE_REQUESTED
    words = t.split()
    if len(words) <= 3:
        if words[0] in CONFIRM_WORDS and not (set(words) & RESCHEDULE_WORDS):
            return ReminderStatus.CONFIRMED
        if words[0] in RESCHEDULE_WORDS and not (set(words) & CONFIRM_WORDS):
            return ReminderStatus.RESCHEDULE_REQUESTED
    return None


def _when(start: datetime, tz: str = "Europe/London") -> str:
    try:
        local = start.astimezone(ZoneInfo(tz))
    except Exception:
        local = start
    return local.strftime("%a %d %b at %H:%M")


class ReminderService:
    def __init__(
        self,
        store: CallStore,
        sms: MessageService,
        *,
        business_name: Callable[[str], Awaitable[str]] | None = None,
        on_ticket: Callable[[str, str, TicketIntake], Awaitable[Ticket]] | None = None,
    ) -> None:
        self.store = store
        self.sms = sms
        self._business_name = business_name
        self.on_ticket = on_ticket

    # -- policy ---------------------------------------------------------------------------------
    async def policy(self, tenant_id: str) -> ReminderPolicy:
        doc = await self.store.get_doc(POLICY_KIND, tenant_id)
        if doc is None:
            return ReminderPolicy(tenant_id=tenant_id)
        return ReminderPolicy.model_validate(doc.data)

    async def set_policy(self, policy: ReminderPolicy) -> ReminderPolicy:
        valid = {h for h in policy.hours_before if 0 < h <= 24 * 14}
        policy.hours_before = sorted(valid, reverse=True)
        await self.store.put_doc(policy.to_doc())
        return policy

    async def business(self, tenant_id: str) -> str:
        if self._business_name is not None:
            return await self._business_name(tenant_id)
        cfgs = await self.store.list_assistants(tenant_id)
        return cfgs[0].business_name if cfgs else "Your appointment"

    # -- lifecycle ------------------------------------------------------------------------------
    async def on_booking(
        self, booking: Booking, now: datetime | None = None
    ) -> list[AppointmentReminder]:
        """Text a confirmation now, then schedule one reminder per offset still in the future."""
        if not booking.phone:
            return []
        pol = await self.policy(booking.tenant_id)
        if pol.confirmation_enabled:
            await self.send_confirmation(booking, pol)
        if not pol.enabled:
            return []
        now = now or datetime.now(UTC)
        out: list[AppointmentReminder] = []
        for h in pol.hours_before:
            send_at = booking.start - timedelta(hours=h)
            if send_at <= now or booking.start <= now:
                continue
            r = AppointmentReminder(
                tenant_id=booking.tenant_id,
                company_id=booking.company_id,
                booking_id=booking.id,
                phone=booking.phone,
                name=booking.name,
                start=booking.start,
                send_at=send_at,
            )
            await self.store.put_doc(r.to_doc())
            out.append(r)
        return out

    async def send_confirmation(self, booking: Booking, pol: ReminderPolicy) -> Message | None:
        if not booking.phone:
            return None
        body = pol.confirmation_template.format(
            business=await self.business(booking.tenant_id),
            when=_when(booking.start, pol.timezone),
            name=booking.name,
            engineer=booking.resource_name or "",
        )
        m = await self.sms.send(
            booking.tenant_id,
            booking.company_id,
            booking.phone,
            body,
            call_id=booking.call_id,
            trigger=SmsTrigger.BOOKING_CONFIRMATION,
        )
        if m.status != MessageStatus.SENT:
            log.info("booking confirmation for %s not sent: %s", booking.id, m.error)
        return m

    async def list_for(self, tenant_id: str, limit: int = 200) -> list[AppointmentReminder]:
        docs = await self.store.list_docs(REMINDER_KIND, tenant_id, limit)
        return [AppointmentReminder.model_validate(d.data) for d in docs]

    async def due(self, now: datetime | None = None) -> list[AppointmentReminder]:
        now = now or datetime.now(UTC)
        docs = await self.store.list_docs(REMINDER_KIND, None, 5000)
        out = []
        for d in docs:
            r = AppointmentReminder.model_validate(d.data)
            if r.status == ReminderStatus.SCHEDULED and r.send_at <= now:
                out.append(r)
        return out

    async def send(
        self, r: AppointmentReminder, now: datetime | None = None
    ) -> AppointmentReminder:
        now = now or datetime.now(UTC)
        booking = await self._booking(r.booking_id)
        dead = booking is None or booking.status in ("cancelled", "reschedule_requested")
        if dead or r.start <= now:
            r.status = ReminderStatus.CANCELLED
            await self.store.put_doc(r.to_doc())
            return r
        pol = await self.policy(r.tenant_id)
        body = pol.template.format(
            business=await self.business(r.tenant_id),
            when=_when(r.start, pol.timezone),
            name=r.name,
            engineer=(booking.resource_name if booking and booking.resource_name else ""),
        )
        m = await self.sms.send(
            r.tenant_id, r.company_id, r.phone, body, trigger=SmsTrigger.APPOINTMENT_REMINDER
        )
        r.message_id = m.id
        r.sent_at = now
        if m.status == MessageStatus.SENT:
            r.status = ReminderStatus.SENT
        else:
            r.status = ReminderStatus.FAILED
            r.error = m.error
        await self.store.put_doc(r.to_doc())
        return r

    async def sweep(self, now: datetime | None = None) -> int:
        n = 0
        for r in await self.due(now):
            sent = await self.send(r, now)
            if sent.status == ReminderStatus.SENT:
                n += 1
        return n

    # -- replies --------------------------------------------------------------------------------
    async def match(
        self, tenant_id: str, phone: str, now: datetime | None = None
    ) -> AppointmentReminder | None:
        """The reminder a reply from ``phone`` most plausibly refers to (latest sent, open)."""
        now = now or datetime.now(UTC)
        best: AppointmentReminder | None = None
        for r in await self.list_for(tenant_id, 1000):
            if r.phone != phone or r.sent_at is None:
                continue
            if r.status not in (ReminderStatus.SENT, ReminderStatus.CONFIRMED):
                continue
            if now - r.sent_at > REPLY_WINDOW or r.start < now - timedelta(hours=1):
                continue
            if best is None or best.sent_at is None or r.sent_at > best.sent_at:
                best = r
        return best

    async def handle_reply(
        self, tenant_id: str, phone: str, text: str, now: datetime | None = None
    ) -> ReminderReply | None:
        """Apply a confirm/reschedule reply; None when it isn't one (let the AI answer)."""
        action = interpret_reply(text)
        if action is None:
            return None
        r = await self.match(tenant_id, phone, now)
        if r is None:
            return None
        now = now or datetime.now(UTC)
        pol = await self.policy(tenant_id)
        business = await self.business(tenant_id)
        booking = await self._booking(r.booking_id)
        first = (r.name or "").split(" ")[0] or "there"
        r.reply, r.replied_at, r.status = text[:160], now, action
        if action == ReminderStatus.CONFIRMED:
            if booking is not None:
                booking.status = "confirmed"
                await self.store.put_doc(booking.to_doc())
            reply = pol.confirm_reply
        else:
            if booking is not None:
                booking.status = "reschedule_requested"
                await self.store.put_doc(booking.to_doc())
            if self.on_ticket is not None:
                try:
                    ticket = await self.on_ticket(
                        tenant_id,
                        r.company_id,
                        TicketIntake(
                            caller_name=r.name,
                            caller_number=phone,
                            reason=(
                                f"Wants to reschedule appointment on "
                                f"{_when(r.start, pol.timezone)} (replied "
                                f"'{text.strip()[:40]}' to the SMS reminder)"
                            ),
                            priority=TicketPriority.NORMAL,
                            category="appointment",
                            source="sms_reminder",
                        ),
                    )
                    r.ticket_id = ticket.id
                except Exception:
                    log.warning("reschedule ticket failed for %s", r.id, exc_info=True)
            reply = pol.reschedule_reply
        if action == ReminderStatus.RESCHEDULE_REQUESTED:
            for other in await self.list_for(tenant_id, 1000):
                if other.booking_id == r.booking_id and other.status == ReminderStatus.SCHEDULED:
                    other.status = ReminderStatus.CANCELLED
                    await self.store.put_doc(other.to_doc())
        await self.store.put_doc(r.to_doc())
        return ReminderReply(
            reminder=r,
            booking=booking,
            action=action,
            text=reply.format(name=first, when=_when(r.start, pol.timezone), business=business),
        )

    async def _booking(self, booking_id: str) -> Booking | None:
        doc = await self.store.get_doc(BOOKING_KIND, booking_id)
        return Booking.model_validate(doc.data) if doc else None


class ReminderLoop:
    def __init__(self, svc: ReminderService, interval_s: float = 60.0) -> None:
        self.svc, self.interval_s = svc, interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self.svc.sweep()
            except Exception:
                log.warning("reminder sweep failed", exc_info=True)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
