"""Contact intelligence (Phase 21e).

Every caller starts as a *prospect*. This module decides when they become a *customer*
(a completed booking, a paid payment, a resolved job ticket, or a CRM saying so), applies the
tenant's VIP rules (repeat-caller count, lifetime value, named accounts) and hands the live
assistant a short, tenant-authorised picture of who is calling so it can greet them by name,
skip details it already holds and follow the VIP treatment the owner configured.

A status set by hand *pins* the contact: the automatic rules never touch a pinned contact, and
every manual change is recorded in the audit log by the route that made it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from parlio_api.screening import digits, same_number
from parlio_api.store import (
    CallFilter,
    CallStore,
    Contact,
    ContactUpdate,
    TenantDoc,
    TicketStatus,
)

log = logging.getLogger("parlio.contacts")

RULES_KIND = "contact_rules"
STATUSES = ("prospect", "customer", "blocked")


class ContactRules(BaseModel):
    """Tenant-configured automation. All thresholds optional; ``None`` disables that rule."""

    auto_promote: bool = True
    promote_on_booking: bool = True
    promote_on_payment: bool = True
    promote_on_resolved_ticket: bool = True
    vip_min_calls: int | None = Field(None, ge=2, le=1000)
    vip_min_value_pence: int | None = Field(None, ge=100)
    vip_named_accounts: list[str] = Field(default_factory=list)  # names or numbers
    vip_instructions: str = (
        "Always offer to put them straight through to a person; if nobody is free, take a"
        " message and mark it high priority."
    )
    customer_instructions: str = ""
    vip_department: str | None = None  # preferred department for VIP transfers


class CallerContext(BaseModel):
    """What the assistant is allowed to know about the caller at call start."""

    known: bool = False
    contact_id: str | None = None
    name: str | None = None
    email_known: bool = False
    status: str = "prospect"
    vip: bool = False
    call_count: int = 0
    last_call_at: datetime | None = None
    last_summary: str | None = None
    open_tickets: list[str] = Field(default_factory=list)
    upcoming_bookings: list[str] = Field(default_factory=list)
    lifetime_value_pence: int = 0
    notes: str | None = None
    vip_instructions: str | None = None
    customer_instructions: str | None = None
    vip_department: str | None = None


def _norm_name(s: str) -> str:
    return " ".join(s.lower().split())


class ContactIntelligence:
    def __init__(self, store: CallStore) -> None:
        self.store = store

    # -- rules --------------------------------------------------------------------------------
    async def rules(self, tenant_id: str) -> ContactRules:
        d = await self.store.get_doc(RULES_KIND, tenant_id)
        return ContactRules.model_validate(d.data) if d else ContactRules()

    async def save_rules(self, tenant_id: str, rules: ContactRules) -> ContactRules:
        await self.store.put_doc(
            TenantDoc(
                kind=RULES_KIND,
                id=tenant_id,
                tenant_id=tenant_id,
                data=rules.model_dump(mode="json"),
            )
        )
        return rules

    # -- lookup ---------------------------------------------------------------------------------
    async def find(self, tenant_id: str, number: str | None) -> Contact | None:
        if not number or len(digits(number)) < 6:
            return None
        for c in await self.store.list_contacts(tenant_id, q=digits(number)[-9:], limit=20):
            if same_number(c.e164, number):
                return c
        return None

    # -- manual changes (always win) ----------------------------------------------------------
    async def set_manual(
        self, contact: Contact, upd: ContactUpdate, *, actor: str
    ) -> Contact | None:
        if upd.status is not None and upd.status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        fields = upd.model_copy()
        if upd.status is not None or upd.vip is not None:
            fields.status_pinned = True
            fields.status_source = actor
        return await self.store.update_contact(contact.id, fields)

    async def unpin(self, contact: Contact) -> Contact | None:
        return await self.store.update_contact(
            contact.id, ContactUpdate(status_pinned=False, status_source="unpinned")
        )

    # -- automatic promotion --------------------------------------------------------------------
    async def promote(
        self,
        tenant_id: str,
        number: str | None,
        *,
        reason: str,
        value_pence: int = 0,
        name: str | None = None,
    ) -> Contact | None:
        """Prospect -> customer unless the contact is pinned or blocked. Adds value regardless."""
        contact = await self.find(tenant_id, number)
        if contact is None:
            return None
        rules = await self.rules(tenant_id)
        upd = ContactUpdate()
        if value_pence > 0:
            upd.lifetime_value_pence = contact.lifetime_value_pence + value_pence
        if name and not contact.name:
            upd.name = name
        if (
            rules.auto_promote
            and not contact.status_pinned
            and contact.status == "prospect"
            and self._promotes(rules, reason)
        ):
            upd.status = "customer"
            upd.status_source = f"auto:{reason}"
        updated = await self.store.update_contact(contact.id, upd)
        if updated is not None:
            updated = await self.apply_vip_rules(updated, rules)
        return updated

    @staticmethod
    def _promotes(rules: ContactRules, reason: str) -> bool:
        return {
            "booking": rules.promote_on_booking,
            "payment": rules.promote_on_payment,
            "ticket_resolved": rules.promote_on_resolved_ticket,
            "crm": True,
        }.get(reason, False)

    def vip_rule_hit(self, contact: Contact, rules: ContactRules) -> str | None:
        if rules.vip_min_calls is not None and contact.call_count >= rules.vip_min_calls:
            return f"{rules.vip_min_calls}+ calls"
        if (
            rules.vip_min_value_pence is not None
            and contact.lifetime_value_pence >= rules.vip_min_value_pence
        ):
            return f"value >= £{rules.vip_min_value_pence / 100:.0f}"
        for acct in rules.vip_named_accounts:
            a = acct.strip()
            if not a:
                continue
            if digits(a) and len(digits(a)) >= 6 and same_number(contact.e164, a):
                return "named account"
            if contact.name and _norm_name(a) == _norm_name(contact.name):
                return "named account"
        return None

    async def apply_vip_rules(self, contact: Contact, rules: ContactRules | None = None) -> Contact:
        if contact.status_pinned or contact.status == "blocked" or contact.vip:
            return contact
        rules = rules or await self.rules(contact.tenant_id)
        hit = self.vip_rule_hit(contact, rules)
        if hit is None:
            return contact
        upd = ContactUpdate(vip=True, status_source=f"auto:vip {hit}")
        if contact.status == "prospect" and rules.auto_promote:
            upd.status = "customer"
        return (await self.store.update_contact(contact.id, upd)) or contact

    async def after_call(self, contact_id: str | None) -> None:
        """Post-call hook: re-evaluate VIP rules once the call count has been bumped."""
        if not contact_id:
            return
        c = await self.store.get_contact(contact_id)
        if c is not None:
            await self.apply_vip_rules(c)

    # -- event hooks ----------------------------------------------------------------------------
    async def on_booking(
        self, tenant_id: str, phone: str | None, *, name: str | None, value_pence: int
    ) -> None:
        await self.promote(tenant_id, phone, reason="booking", value_pence=value_pence, name=name)

    async def on_payment_paid(self, tenant_id: str, phone: str, amount_pence: int) -> None:
        await self.promote(tenant_id, phone, reason="payment", value_pence=amount_pence)

    async def on_ticket_resolved(
        self, tenant_id: str, caller_number: str | None, *, name: str | None
    ) -> None:
        await self.promote(tenant_id, caller_number, reason="ticket_resolved", name=name)

    async def on_crm_status(self, tenant_id: str, phone: str, status: str) -> Contact | None:
        """CRM is authoritative for status unless someone pinned the contact by hand."""
        if status not in STATUSES:
            return None
        contact = await self.find(tenant_id, phone)
        if contact is None or contact.status_pinned or contact.status == status:
            return contact
        return await self.store.update_contact(
            contact.id, ContactUpdate(status=status, status_source="crm")
        )

    # -- call-start context ---------------------------------------------------------------------
    async def caller_context(
        self, tenant_id: str, number: str | None, now: datetime | None = None
    ) -> CallerContext:
        contact = await self.find(tenant_id, number)
        if contact is None:
            return CallerContext()
        now = now or datetime.now(UTC)
        rules = await self.rules(tenant_id)
        ctx = CallerContext(
            known=bool(contact.name or contact.status != "prospect" or contact.vip),
            contact_id=contact.id,
            name=contact.name,
            email_known=bool(contact.email),
            status=contact.status,
            vip=contact.vip,
            call_count=contact.call_count,
            last_call_at=contact.last_seen_at if contact.call_count else None,
            lifetime_value_pence=contact.lifetime_value_pence,
            notes=contact.notes or None,
            vip_department=rules.vip_department if contact.vip else None,
        )
        if contact.vip and rules.vip_instructions.strip():
            ctx.vip_instructions = rules.vip_instructions.strip()
        if contact.status == "customer" and rules.customer_instructions.strip():
            ctx.customer_instructions = rules.customer_instructions.strip()
        for t in await self.store.list_tickets(tenant_id, limit=500):
            if t.status in (TicketStatus.OPEN, TicketStatus.CLAIMED) and (
                t.contact_id == contact.id
                or (t.caller_number and same_number(t.caller_number, contact.e164))
            ):
                who = f" (with {t.assigned_to})" if t.assigned_to else ""
                ctx.open_tickets.append(f"{t.reason.strip()[:120]}{who}")
        for b in await self.store.list_docs("booking", tenant_id, limit=500):
            phone = str(b.data.get("phone") or "")
            start = str(b.data.get("start") or "")
            if not phone or not same_number(phone, contact.e164) or not start:
                continue
            try:
                when = datetime.fromisoformat(start.replace("Z", "+00:00"))
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            if when >= now and str(b.data.get("status", "confirmed")) != "cancelled":
                ctx.upcoming_bookings.append(f"appointment on {when:%a %d %b at %H:%M}")
        ctx.open_tickets = ctx.open_tickets[:3]
        ctx.upcoming_bookings = ctx.upcoming_bookings[:3]
        if contact.call_count:
            last = await self._last_summary(tenant_id, contact)
            ctx.last_summary = last
        return ctx

    async def _last_summary(self, tenant_id: str, contact: Contact) -> str | None:
        calls = await self.store.filter_calls(
            CallFilter(tenant_id=tenant_id, q=contact.e164, limit=5)
        )
        for c in sorted(calls, key=lambda c: c.started_at, reverse=True):
            mine = c.contact_id == contact.id or bool(
                c.caller and same_number(c.caller, contact.e164)
            )
            if mine and c.summary:
                return c.summary.strip()[:300]
        return None


def caller_context_instruction(ctx: CallerContext) -> str | None:
    """Prompt fragment for the worker. Only what the tenant already knows about this caller."""
    if not ctx.known and not ctx.open_tickets and not ctx.upcoming_bookings:
        return None
    lines = ["CALLER CONTEXT (from this business's own records; use it, don't recite it):"]
    label = {"customer": "an existing customer", "blocked": "blocked", "prospect": "a prospect"}
    who = ctx.name or "this caller"
    lines.append(
        f"- {who} is {label.get(ctx.status, ctx.status)}"
        + (", flagged VIP" if ctx.vip else "")
        + (f", {ctx.call_count} previous call(s)" if ctx.call_count else "")
        + "."
    )
    if ctx.name:
        lines.append(
            f'- Greet them by name ("{ctx.name}") and don\'t ask for their name again; confirm'
            " only if something they say suggests it's a different person."
        )
    lines.append(
        "- Their phone number is already on file: don't ask for it, just confirm it's the best"
        " one to call back."
    )
    if ctx.email_known:
        lines.append(
            "- Their email is already on file: don't ask for it unless they want to change it."
        )
    if ctx.last_summary:
        lines.append(f"- Last call: {ctx.last_summary}")
    for t in ctx.open_tickets:
        lines.append(
            f"- Open request: {t}. If they're calling about it, give an update rather than"
            " opening a new one."
        )
    for b in ctx.upcoming_bookings:
        lines.append(f"- Upcoming booking: {b}. Offer to confirm or change it if relevant.")
    if ctx.customer_instructions:
        lines.append(f"- Customer handling: {ctx.customer_instructions}")
    if ctx.vip:
        lines.append(f"- VIP handling: {ctx.vip_instructions or 'prioritise this caller.'}")
        if ctx.vip_department:
            lines.append(f"- Preferred department for VIP transfers: {ctx.vip_department}.")
    if ctx.notes:
        lines.append(f"- Notes: {ctx.notes[:300]}")
    return "\n".join(lines)
