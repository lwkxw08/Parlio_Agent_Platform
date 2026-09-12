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
    TicketIntake,
    TicketPriority,
    TransferMode,
    TransferOutcome,
)
from parlio_voice.transfer import TransferEngine, TransferResult

log = logging.getLogger("parlio.tools")

Emit = Callable[[CallEventType, dict[str, Any]], None]
Say = Callable[[str], Awaitable[None]]


class TicketClient:
    """Thin client for the Core API ticket intake endpoint."""

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


class ReceptionistTools:
    def __init__(
        self,
        cfg: AssistantConfig,
        call_id: str,
        caller: str | None,
        engine: TransferEngine,
        tickets: TicketClient | None,
        emit: Emit,
        say: Say,
    ) -> None:
        self.cfg = cfg
        self.call_id = call_id
        self.caller = caller
        self.engine = engine
        self.tickets = tickets
        self.emit = emit
        self.say = say
        self.urgent_hit: str | None = None
        self.transferred = False
        self.ticket_id: str | None = None

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
        if self.tickets is not None:
            try:
                ticket = await self.tickets.create(self.cfg, intake)
            except Exception:
                log.exception("ticket API failed; ticket will be rebuilt from the event stream")
        self.ticket_id = ticket.get("id")
        self.emit(
            CallEventType.TICKET_CREATED,
            {"ticket_id": self.ticket_id, "intake": intake.model_dump(mode="json")},
        )
        return ticket


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

    return [check_availability, transfer_to_human, create_ticket]


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
