"""LLM function tools the receptionist can call mid-conversation.

`ReceptionistTools` owns the side effects (transfer engine, ticket API, event bus) and exposes
plain async methods; `build_tools()` wraps them as LiveKit `function_tool`s so the same logic is
unit-testable without an AgentSession.
"""

from __future__ import annotations

import logging
import re
import time
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
from parlio_voice.outbound import OutcomeReporter, outcome_tool
from parlio_voice.transfer import TransferEngine, TransferResult

log = logging.getLogger("parlio.tools")

Emit = Callable[[CallEventType, dict[str, Any]], None]
Say = Callable[[str], Awaitable[None]]

_CONNECT_PHRASES = re.compile(
    r"\b(connect(ing)? you|put(ting)? you through|transfer(ring)? you"
    r"|pass(ing)? you (over|through))\b",
    re.IGNORECASE,
)


def spoken_number(number: str) -> str | None:
    """Caller ID as the receptionist should say it: UK national format, single digits
    separated by spaces so the TTS never reads '934' as 'nine hundred and thirty-four'."""
    digits = re.sub(r"\D", "", number)
    if not digits or len(digits) < 7:
        return None
    if digits.startswith("44"):
        digits = "0" + digits[2:]
    if digits.startswith("07") and len(digits) == 11:
        groups = [digits[:5], digits[5:8], digits[8:]]
    elif digits.startswith("02") and len(digits) == 11:
        groups = [digits[:3], digits[3:7], digits[7:]]
    elif digits.startswith("01") and len(digits) == 11:
        groups = [digits[:5], digits[5:8], digits[8:]]
    else:
        groups = [digits[i : i + 3] for i in range(0, len(digits), 3)]
    return ", ".join(" ".join(g) for g in groups)


def normalise_number(spoken: str | None, caller: str | None) -> str | None:
    """Dialable E.164 from what the LLM passed (often the spoken read-back with spaces/commas).
    Falls back to caller ID when nothing usable was given or the digits match it."""
    digits = re.sub(r"\D", "", spoken or "")
    if len(digits) < 7:
        return caller
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = "44" + digits[1:]
    if caller and re.sub(r"\D", "", caller) == digits:
        return caller
    return f"+{digits}"


def caller_id_instruction(caller: str | None) -> str:
    """Prompt section so the assistant offers the caller's own number instead of asking for one."""
    said = spoken_number(caller) if caller else None
    if said is None:
        return (
            "The caller's number is withheld or unknown: if you need a callback number, ask for "
            "it and read it back in groups to confirm."
        )
    return (
        f"The caller is ringing from {said}. When you need a callback number, do not ask them "
        f"to give one; ask 'Can we use the number you're calling from, {said}, if we need to "
        "call you back?' If they say yes, that is confirmed. If they say no, ask for the best "
        "number and read it back the same way (single digits, spaces between them) to confirm."
    )


def mentions_connecting(text: str) -> bool:
    return bool(_CONNECT_PHRASES.search(text))


def guess_department(text: str, departments: list[str]) -> str | None:
    low = text.lower()
    return next((d for d in departments if d.lower() in low), None)


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

    async def payment_link(self, cfg: AssistantConfig, req: dict[str, Any]) -> dict[str, Any]:
        r = await self._http.post(
            "/v1/worker/payments/link", json={"assistant_id": cfg.assistant_id, **req}
        )
        r.raise_for_status()
        return dict(r.json())

    async def verify_caller(self, cfg: AssistantConfig, req: dict[str, Any]) -> dict[str, Any]:
        """Answers travel in the request body only and are hashed server-side; never logged."""
        r = await self._http.post(
            "/v1/worker/verification/check", json={"assistant_id": cfg.assistant_id, **req}
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

    async def request_approval(self, cfg: AssistantConfig, req: dict[str, Any]) -> dict[str, Any]:
        r = await self._http.post(
            "/v1/worker/approvals",
            params={"tenant_id": cfg.tenant_id, "company_id": cfg.company_id},
            json=req,
        )
        r.raise_for_status()
        return dict(r.json())

    async def poll_approval(
        self, cfg: AssistantConfig, approval_id: str, wait_s: float
    ) -> dict[str, Any]:
        r = await self._http.get(
            f"/v1/worker/approvals/{approval_id}",
            params={"tenant_id": cfg.tenant_id, "wait_s": wait_s},
            timeout=wait_s + 10,
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
        api: CoreApiClient | None,
        emit: Emit,
        say: Say,
        reporter: OutcomeReporter | None = None,
    ) -> None:
        self.reporter = reporter
        self.cfg = cfg
        self.call_id = call_id
        self.caller = caller
        self.engine = engine
        self.api = api
        self.emit = emit
        self.say = say
        self.urgent_hit: str | None = None
        self.transferred = False
        self.transfer_attempted = False
        self.ticket_id: str | None = None
        self.booking_id: str | None = None
        self.sms_sent: list[str] = []
        self.approvals: list[dict[str, Any]] = []
        self.verified = False
        self.verification_locked = False
        self.payment_ids: list[str] = []

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
        self.transfer_attempted = True
        await self.say(self.holding_line(department))
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

    def holding_line(self, department: str | None) -> str:
        who = f"the {department} team" if department else "someone"
        return f"Connecting you to {who} now. Please bear with me, this can take a moment."

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
            caller_number=normalise_number(callback_number, self.caller),
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

    # -- payments & verification (Phase 12) --------------------------------------------------
    async def verify_caller(self, answers: dict[str, str]) -> dict[str, Any]:
        """Check DOB / postcode / reference against the contact record. Only the outcome comes
        back and only the outcome is emitted; the answers themselves never leave the request."""
        if self.api is None:
            return {"outcome": "unavailable"}
        if self.verification_locked:
            return {"outcome": "locked", "attempts_left": 0}
        allowed = {f.value for f in self.cfg.verification.fields}
        clean = {k: v.strip() for k, v in answers.items() if k in allowed and v and v.strip()}
        if not clean:
            return {"outcome": "failed", "error": "no answers for the configured fields"}
        req = {"call_id": self.call_id, "caller": self.caller, "answers": clean}
        try:
            res = await self.api.verify_caller(self.cfg, req)
        except Exception as e:
            log.warning("verification failed: %s", type(e).__name__)
            return {"outcome": "unavailable"}
        outcome = res.get("outcome")
        if outcome == "verified":
            self.verified = True
        if outcome == "locked":
            self.verification_locked = True
        self.emit(
            CallEventType.CALLER_VERIFIED,
            {
                "outcome": outcome,
                "fields": sorted(clean),
                "attempts_left": res.get("attempts_left"),
            },
        )
        return {
            "outcome": outcome,
            "attempts_left": res.get("attempts_left"),
            "matched": res.get("matched"),
        }

    async def send_payment_link(
        self, amount: float, description: str, to: str | None, consent: bool
    ) -> dict[str, Any]:
        if self.api is None or not self.cfg.payments.enabled:
            return {"status": "unavailable"}
        if not consent:
            return {"status": "failed", "error": "caller has not agreed to receive a text"}
        dest = to or self.caller
        if not dest or dest.startswith(("anonymous", "web:")) or dest == "unknown":
            return {"status": "failed", "error": "need a mobile number to text the link to"}
        pence = round(amount * 100)
        if pence <= 0:
            return {"status": "failed", "error": "amount must be positive"}
        if pence > self.cfg.payments.max_pence:
            return {
                "status": "failed",
                "error": f"amount above the limit of {self.cfg.payments.max_pence / 100:.2f}",
            }
        vc = self.cfg.verification
        if vc.enabled and vc.required_for_payments and not self.verified:
            return {"status": "failed", "error": "verify the caller first (verify_caller)"}
        req = {
            "call_id": self.call_id,
            "to": dest,
            "amount_pence": pence,
            "description": description.strip()[:200],
            "consent": True,
            "verified_caller": self.verified,
        }
        try:
            res = await self.api.payment_link(self.cfg, req)
        except httpx.HTTPStatusError as e:
            detail = e.response.json().get("detail") if e.response.content else None
            return {"status": "failed", "error": detail or "payment link refused"}
        except Exception as e:
            log.warning("payment link failed: %s", type(e).__name__)
            return {"status": "failed", "error": "payments unavailable"}
        if res.get("id"):
            self.payment_ids.append(str(res["id"]))
        self.emit(
            CallEventType.PAYMENT_REQUESTED,
            {
                "payment_id": res.get("id"),
                "amount_pence": pence,
                "currency": self.cfg.payments.currency,
                "status": res.get("status"),
                "sms_status": res.get("sms_status"),
            },
        )
        return {
            "status": res.get("sms_status") or res.get("status"),
            "amount": res.get("amount_display"),
            "expires_at": res.get("expires_at"),
        }

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


APPROVAL_WAIT_S = 90.0


async def request_owner_approval(
    t: ReceptionistTools,
    kind: str,
    title: str,
    details: str,
    amount: float | None,
    wait_s: float = APPROVAL_WAIT_S,
) -> dict[str, Any]:
    """Ask the business owner to approve a quote/booking/refund; waits for a tap (or times out).

    The caller hears a holding line first; the LLM tells the caller the outcome afterwards.
    """
    if t.api is None:
        return {"status": "unavailable", "error": "approvals not configured"}
    req = {
        "call_id": t.call_id,
        "kind": kind if kind in ("quote", "booking", "refund", "discount") else "other",
        "title": title[:200],
        "details": details[:2000],
        "amount": amount,
        "caller": t.caller,
        "timeout_s": int(wait_s) + 30,
    }
    try:
        ap = await t.api.request_approval(t.cfg, req)
    except Exception as e:
        log.warning("approval request failed: %s", e)
        return {"status": "unavailable", "error": "could not reach the owner"}
    t.approvals.append(ap)
    t.emit(CallEventType.APPROVAL_REQUESTED, {"approval_id": ap.get("id"), "title": title})
    await t.say("Let me just check that with the team, bear with me a moment.")
    deadline = time.monotonic() + wait_s
    status = str(ap.get("status", "pending"))
    while status == "pending" and time.monotonic() < deadline:
        chunk = min(20.0, max(1.0, deadline - time.monotonic()))
        try:
            ap = await t.api.poll_approval(t.cfg, str(ap["id"]), chunk)
        except Exception as e:
            log.warning("approval poll failed: %s", e)
            break
        status = str(ap.get("status", "pending"))
    if status == "pending":
        status = "timed_out"
    return {"status": status, "note": ap.get("note"), "approval_id": ap.get("id")}


def build_tools(t: ReceptionistTools) -> list[Any]:
    """Wrap `ReceptionistTools` methods as LLM-callable function tools."""
    cfg = t.cfg.transfer
    depts = cfg.describe_departments() or "general"
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
            "Transfer the caller to a human. Call it immediately when the caller asks for a "
            "person, when the matter is urgent, or when you cannot help - in the same turn, "
            "without announcing it first (the tool tells the caller it is connecting them). "
            f"Departments: {depts}. Returns the outcome; if not 'answered', tell the caller "
            "nobody could pick up and offer to take a message (create_ticket)."
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

    tools = [check_availability, transfer_to_human, create_ticket]
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

    if t.api is not None:

        @function_tool(
            name="request_owner_approval",
            description=(
                "Ask the business owner to approve something you are not allowed to decide "
                "yourself: a price quote, a discount, a refund, or a booking outside normal "
                "rules. Tell the caller you are checking first. Waits up to 90 seconds for the "
                "owner to tap approve/reject; result is approved, rejected or timed_out. If "
                "timed_out, offer to take a message and call back."
            ),
        )
        async def request_owner_approval_tool(
            kind: str, title: str, details: str, amount: float | None = None
        ) -> dict[str, Any]:
            return await request_owner_approval(t, kind, title, details, amount)

        tools.append(request_owner_approval_tool)

    if t.api is not None and t.cfg.verification.enabled:
        vfields = ", ".join(f.value for f in t.cfg.verification.fields)

        @function_tool(
            name="verify_caller",
            description=(
                "Verify the caller's identity before discussing account details or taking a "
                f"payment. Ask for these in turn: {vfields}. Pass what they said as answers "
                "(keys: dob as YYYY-MM-DD or as spoken, postcode, reference). Returns verified, "
                "failed (with attempts_left), locked or no_record. Never repeat the values back "
                "in full; if locked, offer a callback from a staff member."
            ),
        )
        async def verify_caller(
            dob: str | None = None, postcode: str | None = None, reference: str | None = None
        ) -> dict[str, Any]:
            answers = {
                k: v
                for k, v in {"dob": dob, "postcode": postcode, "reference": reference}.items()
                if v
            }
            return await t.verify_caller(answers)

        tools.append(verify_caller)

    if t.api is not None and t.cfg.payments.enabled:
        limit = t.cfg.payments.max_pence / 100

        @function_tool(
            name="send_payment_link",
            description=(
                "Text the caller a secure payment link (card details are entered on the hosted "
                f"payment page, never spoken to you). Amount in {t.cfg.payments.currency.upper()}"
                f", maximum {limit:.2f}. Only after the caller explicitly agrees to receive the "
                "text (consent=true) and you have confirmed the amount and mobile number. Do "
                "not ask for card numbers under any circumstances."
            ),
        )
        async def send_payment_link(
            amount: float, description: str, consent: bool, to: str | None = None
        ) -> dict[str, Any]:
            return await t.send_payment_link(amount, description, to, consent)

        tools.append(send_payment_link)

    if t.reporter is not None:
        tools.append(outcome_tool(t.reporter))

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
