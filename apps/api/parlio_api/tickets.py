"""Ticket/callback queue: intake -> classified ticket with SLA -> alerts -> SLA escalation.

Classification is heuristic (keywords) so it works with no LLM key; `sla_minutes` per priority
comes from the assistant's `TransferConfig`. Alerts go through `Notifier` (log or webhook now;
Slack app / SMS / email arrive in Phase 5 behind the same interface).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

import httpx

from parlio_api.store import CallStore, Ticket, TicketEvent, intake_from_event
from parlio_voice.models import CallEvent, TicketIntake, TicketPriority, TransferConfig

log = logging.getLogger("parlio.api.tickets")

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "emergency": ("emergency", "leak", "flood", "burst", "gas", "fire", "no heating", "no power"),
    "booking": ("book", "appointment", "schedule", "reschedule", "cancel", "availability"),
    "quote": ("quote", "price", "cost", "estimate", "how much"),
    "billing": ("invoice", "bill", "payment", "refund", "charge", "pay"),
    "complaint": ("complain", "unhappy", "disappointed", "angry", "poor", "terrible"),
    "fault": ("not working", "broken", "fault", "issue", "problem", "error"),
    "sales": ("interested", "new customer", "sign up", "buy", "purchase"),
}

URGENT_HINTS = ("urgent", "asap", "immediately", "right now", "emergency", "today")


def classify_category(text: str) -> str:
    low = text.lower()
    for cat, words in CATEGORY_KEYWORDS.items():
        if any(w in low for w in words):
            return cat
    return "general"


def infer_priority(intake: TicketIntake, category: str) -> TicketPriority:
    if category == "emergency":
        return TicketPriority.URGENT
    if intake.priority != TicketPriority.NORMAL:
        return intake.priority
    low = intake.reason.lower()
    if any(h in low for h in URGENT_HINTS):
        return TicketPriority.HIGH
    return TicketPriority.NORMAL


def infer_department(intake: TicketIntake, category: str, cfg: TransferConfig | None) -> str | None:
    if intake.department:
        return intake.department
    if cfg is None:
        return None
    depts = {d.lower(): d for d in cfg.departments()}
    for candidate in (category, "emergencies" if category == "emergency" else ""):
        if candidate in depts:
            return depts[candidate]
    return next(iter(depts.values()), None)


def build_ticket(
    tenant_id: str,
    company_id: str,
    intake: TicketIntake,
    cfg: TransferConfig | None,
    now: datetime | None = None,
) -> Ticket:
    now = now or datetime.now(UTC)
    category = intake.category or classify_category(intake.reason)
    priority = infer_priority(intake, category)
    sla_map = cfg.sla_minutes if cfg else TransferConfig().sla_minutes
    minutes = sla_map.get(priority, 240)
    return Ticket(
        id=f"tk-{uuid4().hex[:10]}",
        tenant_id=tenant_id,
        company_id=company_id,
        call_id=intake.call_id,
        priority=priority,
        category=category,
        department=infer_department(intake, category, cfg),
        caller_name=intake.caller_name,
        caller_number=intake.caller_number,
        reason=intake.reason,
        callback_window=intake.callback_window,
        source=intake.source,
        sla_due_at=now + timedelta(minutes=minutes),
        created_at=now,
        updated_at=now,
    )


class Notifier(Protocol):
    async def send(self, tenant_id: str, level: str, title: str, body: str) -> None: ...


class LogNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str]] = []

    async def send(self, tenant_id: str, level: str, title: str, body: str) -> None:
        self.sent.append((tenant_id, level, title, body))
        log.info("notify[%s] %s: %s - %s", level, tenant_id, title, body)


class WebhookNotifier:
    """Posts Slack-incoming-webhook JSON to one URL (per-tenant routing arrives in Phase 5)."""

    def __init__(self, url: str, fallback: Notifier | None = None) -> None:
        self._url = url
        self._http = httpx.AsyncClient(timeout=5.0)
        self._fallback = fallback or LogNotifier()

    async def send(self, tenant_id: str, level: str, title: str, body: str) -> None:
        payload = {"text": f"[{level.upper()}] {title}\n{body}", "tenant_id": tenant_id}
        try:
            r = await self._http.post(self._url, json=payload)
            r.raise_for_status()
        except httpx.HTTPError:
            log.warning("webhook notify failed; falling back to log", exc_info=True)
            await self._fallback.send(tenant_id, level, title, body)

    async def aclose(self) -> None:
        await self._http.aclose()


def ticket_summary_line(t: Ticket) -> str:
    who = t.caller_name or "Unknown caller"
    num = f" ({t.caller_number})" if t.caller_number else ""
    due = f" | due {t.sla_due_at:%H:%M %d %b}" if t.sla_due_at else ""
    return f"{who}{num}: {t.reason} [{t.priority}/{t.category or 'general'}]{due}"


class TicketService:
    def __init__(self, store: CallStore, notifier: Notifier) -> None:
        self.store = store
        self.notifier = notifier

    async def create_from_intake(
        self, tenant_id: str, company_id: str, intake: TicketIntake
    ) -> Ticket:
        cfg: TransferConfig | None = None
        if intake.call_id:
            call = await self.store.get_call(intake.call_id)
            if call is not None:
                a = await self.store.get_assistant(call.assistant_id)
                cfg = a.transfer if a else None
        ticket = build_ticket(tenant_id, company_id, intake, cfg)
        if intake.caller_number:
            ticket.contact_id, _ = await self.store.touch_contact(
                tenant_id, company_id, intake.caller_number
            )
        await self.store.create_ticket(ticket)
        level = "urgent" if ticket.priority == TicketPriority.URGENT else "info"
        await self.notifier.send(
            tenant_id,
            level,
            f"New {ticket.priority} ticket {ticket.id}",
            ticket_summary_line(ticket),
        )
        return ticket

    async def rebuild_from_event(self, ev: CallEvent) -> Ticket | None:
        """Worker couldn't reach the ticket API mid-call: recreate the ticket from the event."""
        intake = intake_from_event(ev)
        if intake is None:
            return None
        return await self.create_from_intake(ev.tenant_id, ev.company_id, intake)

    async def escalate_overdue(self, now: datetime | None = None) -> list[Ticket]:
        now = now or datetime.now(UTC)
        breached = await self.store.overdue_tickets(now)
        for t in breached:
            await self.store.mark_sla_breached(t.id)
            await self.store.add_ticket_event(
                TicketEvent(ticket_id=t.id, type="sla_breached", note="SLA deadline passed", at=now)
            )
            await self.notifier.send(
                t.tenant_id,
                "escalation",
                f"SLA breached on ticket {t.id}",
                f"{ticket_summary_line(t)} | assigned: {t.assigned_to or 'nobody'}",
            )
        return breached


class SlaMonitor:
    """Background loop that flags overdue tickets and escalates."""

    def __init__(self, service: TicketService, interval_s: float = 30.0) -> None:
        self._svc = service
        self._interval = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="sla-monitor")

    async def _run(self) -> None:
        while True:
            try:
                await self._svc.escalate_overdue()
            except Exception:
                log.exception("sla monitor tick failed")
            await asyncio.sleep(self._interval)

    async def aclose(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
