"""LLM function tools the receptionist can call mid-conversation.

`ReceptionistTools` owns the side effects (transfer engine, ticket API, event bus) and exposes
plain async methods; `build_tools()` wraps them as LiveKit `function_tool`s so the same logic is
unit-testable without an AgentSession.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from livekit.agents import function_tool

from parlio_voice.models import (
    AfterHoursBehaviour,
    AssistantConfig,
    CallEventType,
    SmsTrigger,
    TicketIntake,
    TicketPriority,
    TransferMode,
    TransferOutcome,
)
from parlio_voice.transfer import TransferEngine, TransferResult

log = logging.getLogger("parlio.tools")

Emit = Callable[[CallEventType, dict[str, Any]], None]
Say = Callable[[str], Awaitable[None]]


class CoreApiClient:
    """Thin client for the Core API worker endpoints (tickets, SMS, calendar, SIP admission)."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def create(self, cfg: AssistantConfig, intake: TicketIntake) -> dict[str, Any]:
        r = await self._http.post(
            "/v1/worker/tickets",
            params={"tenant_id": cfg.tenant_id, "company_id": cfg.company_id},
            json=intake.model_dump(mode="json"),
        )
        r.raise_for_status()
        return dict(r.json())

    async def send_sms(
        self,
        cfg: AssistantConfig,
        to: str,
        call_id: str,
        trigger: SmsTrigger,
        context: dict[str, Any],
        body: str | None = None,
    ) -> dict[str, Any] | None:
        r = await self._http.post(
            "/v1/worker/sms",
            json={
                "assistant_id": cfg.assistant_id,
                "to": to,
                "call_id": call_id,
                "trigger": trigger,
                "body": body,
                "context": context,
            },
        )
        r.raise_for_status()
        data = r.json()
        return dict(data) if data else None

    async def availability(self, cfg: AssistantConfig, days: int = 7) -> dict[str, Any]:
        r = await self._http.get(
            "/v1/worker/calendar/availability",
            params={"tenant_id": cfg.tenant_id, "days": days},
        )
        r.raise_for_status()
        return dict(r.json())

    async def book(self, cfg: AssistantConfig, req: dict[str, Any]) -> dict[str, Any]:
        r = await self._http.post(
            "/v1/worker/calendar/bookings", params={"tenant_id": cfg.tenant_id}, json=req
        )
        r.raise_for_status()
        return dict(r.json())

    async def admit(self, number: str, call_id: str) -> dict[str, Any]:
        r = await self._http.post(
            "/v1/worker/telephony/admit", params={"number": number, "call_id": call_id}
        )
        r.raise_for_status()
        return dict(r.json())

    async def release(self, call_id: str) -> None:
        r = await self._http.post("/v1/worker/telephony/release", params={"call_id": call_id})
        r.raise_for_status()


class ReceptionistTools:
    def __init__(
        self,
        cfg: AssistantConfig,
        call_id: str,
        caller: str | None,
        engine: TransferEngine,
        api: CoreApiClient | None,
        emit: Emit,
        say: Say,
    ) -> None:
        self.cfg = cfg
        self.call_id = call_id
        self.caller = caller
        self.engine = engine
        self.api = api
        self.emit = emit
        self.say = say
        self.urgent_hit: str | None = None
        self.transferred = False
        self.ticket_id: str | None = None
        self.booking_id: str | None = None
        self.sms_sent: list[str] = []

    def sms_triggers(self) -> list[SmsTrigger]:
        """Scenarios the LLM may fire mid-call (post-call ones are sent by the API)."""
        auto = {SmsTrigger.AFTER_CALL, SmsTrigger.MISSED_CALL, SmsTrigger.TICKET_CONFIRMATION}
        return sorted(
            {s.trigger for s in self.cfg.sms_scenarios if s.enabled and s.trigger not in auto}
        )

    # -- urgent keyword detection (called from transcript hook) -----------------------------
    def observe_user_text(self, text: str) -> str | None:
        if self.urgent_hit:
            return None
        hit = self.cfg.transfer.matches_urgent(text)
        if hit:
            self.urgent_hit = hit
            self.emit(CallEventType.ESCALATION, {"keyword": hit, "text": text})
            log.info("urgent keyword '%s' on call %s", hit, self.call_id)
        return hit

    # -- availability -------------------------------------------------------------------------
    def availability(self, department: str | None = None) -> dict[str, Any]:
        t = self.cfg.transfer
        cands = t.candidates(department, self.engine.now, urgent=bool(self.urgent_hit))
        return {
            "department": department or "any",
            "departments": t.departments(),
            "someone_available": bool(cands),
            "available": [d.name for d in cands],
        }

    # -- transfer -----------------------------------------------------------------------------
    async def transfer(self, department: str | None, reason: str) -> TransferResult:
        urgent = bool(self.urgent_hit)
        plan = self.engine.plan(department, urgent)
        if not plan:
            res = TransferResult(outcome=TransferOutcome.UNAVAILABLE)
            self.emit(
                CallEventType.TRANSFER_COMPLETED,
                {"outcome": res.outcome, "department": department, "reason": reason},
            )
            return res
        self.emit(
            CallEventType.TRANSFER_STARTED,
            {
                "department": department,
                "reason": reason,
                "urgent": urgent,
                "plan": [d.id for d in plan],
            },
        )
        res = await self.engine.run(department, urgent=urgent)
        for a in res.attempts:
            self.emit(
                CallEventType.TRANSFER_COMPLETED,
                {
                    "transfer_id": a.transfer_id,
                    "destination_id": a.destination.id,
                    "destination": a.destination.name,
                    "department": a.destination.department,
                    "mode": a.mode,
                    "outcome": a.outcome,
                    "started_at": a.started_at.isoformat(),
                    "ended_at": a.ended_at.isoformat(),
                    "reason": reason,
                },
            )
        self.transferred = res.succeeded
        if res.succeeded and res.connected and self.cfg.transfer.mode == TransferMode.WARM:
            await self.say(self.briefing(res.connected.name, reason))
            await self.engine.bridge.leave()
        return res

    def briefing(self, human: str, reason: str) -> str:
        who = self.caller or "a caller"
        urgent = " This is flagged as urgent." if self.urgent_hit else ""
        return (
            f"Hi {human}, this is {self.cfg.name} from {self.cfg.business_name}. "
            f"I have {who} on the line about: {reason}.{urgent} Connecting you now."
        )

    # -- tickets ------------------------------------------------------------------------------
    async def create_ticket(
        self,
        reason: str,
        caller_name: str | None,
        callback_number: str | None,
        urgency: str,
        callback_window: str | None,
        department: str | None,
        source: str = "ai_intake",
    ) -> dict[str, Any]:
        try:
            priority = TicketPriority(urgency.lower())
        except ValueError:
            priority = TicketPriority.NORMAL
        if self.urgent_hit and priority != TicketPriority.URGENT:
            priority = TicketPriority.URGENT
        intake = TicketIntake(
            call_id=self.call_id,
            caller_name=caller_name,
            caller_number=callback_number or self.caller,
            reason=reason,
            priority=priority,
            department=department,
            callback_window=callback_window,
            source=source,
        )
        ticket: dict[str, Any] = {"id": None, "status": "unsent"}
        if self.api is not None:
            try:
                ticket = await self.api.create(self.cfg, intake)
            except Exception:
                log.exception("ticket API failed; ticket will be rebuilt from the event stream")
        self.ticket_id = ticket.get("id")
        self.emit(
            CallEventType.TICKET_CREATED,
            {"ticket_id": self.ticket_id, "intake": intake.model_dump(mode="json")},
        )
        return ticket

    # -- SMS ----------------------------------------------------------------------------------
    async def send_sms(
        self, trigger: str, to: str | None = None, caller_name: str | None = None
    ) -> dict[str, Any]:
        try:
            trig = SmsTrigger(trigger)
        except ValueError:
            return {"status": "failed", "error": f"unknown trigger {trigger}"}
        dest = to or self.caller
        if not dest or dest.startswith("anonymous") or dest == "unknown":
            return {"status": "failed", "error": "no mobile number for the caller"}
        if self.api is None:
            return {"status": "unsent"}
        ctx: dict[str, Any] = {"caller_name": caller_name, "ticket_id": self.ticket_id}
        try:
            msg = await self.api.send_sms(self.cfg, dest, self.call_id, trig, ctx)
        except Exception as e:
            log.warning("sms send failed: %s", e)
            return {"status": "failed", "error": "messaging unavailable"}
        if msg is None:
            return {"status": "skipped", "error": "no enabled scenario for this trigger"}
        if msg.get("status") == "sent":
            self.sms_sent.append(trig)
        return {"status": msg.get("status"), "error": msg.get("error")}

    # -- calendar -----------------------------------------------------------------------------
    async def calendar_availability(self, days: int = 7) -> dict[str, Any]:
        if self.api is None:
            return {"slots": [], "error": "calendar unavailable"}
        try:
            res = await self.api.availability(self.cfg, days)
        except Exception as e:
            log.warning("availability failed: %s", e)
            return {"slots": [], "error": "calendar unavailable"}
        slots = [s["start"] for s in res.get("slots", [])][:8]
        return {
            "slots": slots,
            "booking_url": res.get("booking_url"),
            "error": res.get("error"),
        }

    async def book_appointment(
        self, start: str, name: str, phone: str | None = None, notes: str | None = None
    ) -> dict[str, Any]:
        if self.api is None:
            return {"status": "unsent"}
        req = {
            "start": start,
            "name": name,
            "phone": phone or self.caller,
            "notes": notes,
            "call_id": self.call_id,
        }
        try:
            booking = await self.api.book(self.cfg, req)
        except httpx.HTTPStatusError as e:
            detail = e.response.json().get("detail") if e.response.content else None
            return {"status": "failed", "error": detail or "slot unavailable"}
        except Exception as e:
            log.warning("booking failed: %s", e)
            return {"status": "failed", "error": "calendar unavailable"}
        self.booking_id = booking.get("id")
        return {"status": "booked", "booking_id": self.booking_id, "start": booking.get("start")}


def build_tools(t: ReceptionistTools) -> list[Any]:
    """Wrap `ReceptionistTools` methods as LLM-callable function tools."""
    cfg = t.cfg.transfer
    depts = ", ".join(cfg.departments()) or "general"
    intake_desc = "; ".join(f"{f.name}: {f.prompt}" for f in cfg.intake)

    @function_tool(
        name="check_availability",
        description=(
            "Check whether a human is available right now to take the call. "
            f"Departments: {depts}. Call this before offering to transfer."
        ),
    )
    async def check_availability(department: str | None = None) -> dict[str, Any]:
        return t.availability(department)

    @function_tool(
        name="transfer_to_human",
        description=(
            "Transfer the caller to a human. Use when the caller asks for a person, when the "
            "matter is urgent, or when you cannot help. Tell the caller you are connecting them "
            f"first. Departments: {depts}. Returns the outcome; if not 'answered', offer to take "
            "a message (create_ticket)."
        ),
    )
    async def transfer_to_human(reason: str, department: str | None = None) -> dict[str, Any]:
        res = await t.transfer(department, reason)
        return {
            "outcome": res.outcome,
            "connected_to": res.connected.name if res.connected else None,
            "attempts": len(res.attempts),
        }

    @function_tool(
        name="create_ticket",
        description=(
            "Log a callback ticket for the team when no human is available, the caller prefers a "
            "callback, or after a failed transfer. Collect these first: "
            f"{intake_desc}. Confirm the details back to the caller before calling this."
        ),
    )
    async def create_ticket(
        reason: str,
        caller_name: str | None = None,
        callback_number: str | None = None,
        urgency: str = "normal",
        callback_window: str | None = None,
        department: str | None = None,
    ) -> dict[str, Any]:
        ticket = await t.create_ticket(
            reason, caller_name, callback_number, urgency, callback_window, department
        )
        return {"ticket_id": ticket.get("id"), "status": ticket.get("status")}

    tools = [check_availability, transfer_to_human, create_ticket] if cfg.enabled else []
    triggers = t.sms_triggers()
    if triggers:

        @function_tool(
            name="send_sms",
            description=(
                "Text the caller a pre-approved message. Triggers: "
                f"{', '.join(triggers)}. Only after the caller agrees to receive a text; "
                "confirm the mobile number if the caller ID is withheld."
            ),
        )
        async def send_sms(
            trigger: str, to: str | None = None, caller_name: str | None = None
        ) -> dict[str, Any]:
            return await t.send_sms(trigger, to, caller_name)

        tools.append(send_sms)

    if t.api is not None:

        @function_tool(
            name="check_calendar",
            description=(
                "Get the next free appointment slots (ISO timestamps, local business hours). "
                "Offer the caller two or three options. If a booking_url is returned instead, "
                "offer to text the link with send_sms(trigger='booking_link')."
            ),
        )
        async def check_calendar(days: int = 7) -> dict[str, Any]:
            return await t.calendar_availability(days)

        @function_tool(
            name="book_appointment",
            description=(
                "Book one of the slots from check_calendar. Confirm the time, name and phone "
                "number back to the caller first. start must be one of the returned slots."
            ),
        )
        async def book_appointment(
            start: str, name: str, phone: str | None = None, notes: str | None = None
        ) -> dict[str, Any]:
            return await t.book_appointment(start, name, phone, notes)

        tools.extend([check_calendar, book_appointment])

    return tools


def after_hours_instruction(cfg: AssistantConfig, someone_available: bool) -> str:
    """Extra system guidance appended when nobody is available."""
    if someone_available:
        return ""
    match cfg.transfer.after_hours:
        case AfterHoursBehaviour.TICKET:
            return (
                " No staff are available right now. Do not offer a transfer; instead take a "
                "message using create_ticket and promise a callback."
            )
        case AfterHoursBehaviour.VOICEMAIL:
            return " No staff are available right now. Offer to take a detailed message."
        case AfterHoursBehaviour.BOTH:
            return (
                " No staff are available right now. Take a message with create_ticket and "
                "let the caller know they can also leave a voicemail."
            )
