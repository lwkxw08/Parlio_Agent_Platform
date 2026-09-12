"""SMS during and after calls, driven by the assistant's `SmsScenario`s.

`SmsProvider` is the carrier seam (Telnyx now; another carrier later for failover). Every send,
including skipped/failed ones, is recorded as a `Message` document so the dashboard can show a
per-call message log and the caller never receives duplicates for the same trigger.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from parlio_api.store import CallRecord, CallStore, TenantDoc, Ticket
from parlio_api.telephony.base import TelephonyProvider
from parlio_voice.models import AssistantConfig, SmsScenario, SmsTrigger

log = logging.getLogger("parlio.api.messaging")

KIND = "message"
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class MessageStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class Message(BaseModel):
    id: str = Field(default_factory=lambda: f"msg-{uuid4().hex[:10]}")
    tenant_id: str
    company_id: str
    call_id: str | None = None
    contact_id: str | None = None
    to: str
    sender: str | None = None
    body: str
    trigger: SmsTrigger = SmsTrigger.CUSTOM
    scenario_id: str | None = None
    status: MessageStatus = MessageStatus.SENT
    provider: str | None = None
    provider_ref: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )

    @classmethod
    def from_doc(cls, d: TenantDoc) -> Message:
        return cls.model_validate(d.data)


class SendSmsRequest(BaseModel):
    """Worker/dashboard request: either a scenario trigger or free text."""

    to: str = Field(min_length=6)
    call_id: str | None = None
    trigger: SmsTrigger | None = None
    body: str | None = Field(default=None, max_length=1600)
    context: dict[str, Any] = Field(default_factory=dict)


def render_template(template: str, ctx: dict[str, Any]) -> str:
    """`{placeholder}` substitution that tolerates unknown/missing keys (renders as empty)."""

    def sub(m: re.Match[str]) -> str:
        v = ctx.get(m.group(1))
        return "" if v is None else str(v)

    return re.sub(r"\s{2,}", " ", _PLACEHOLDER.sub(sub, template)).strip()


def scenario_for(cfg: AssistantConfig, trigger: SmsTrigger) -> SmsScenario | None:
    return next((s for s in cfg.sms_scenarios if s.enabled and s.trigger == trigger), None)


def base_context(cfg: AssistantConfig) -> dict[str, Any]:
    return {"business_name": cfg.business_name, "assistant_name": cfg.name}


class SmsProvider(Protocol):
    name: str

    async def send(self, from_e164: str, to_e164: str, body: str) -> str: ...


class LogSmsProvider:
    """Dev/test provider: records instead of sending."""

    name = "log"

    def __init__(self, fail_numbers: set[str] | None = None) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail_numbers = fail_numbers or set()

    async def send(self, from_e164: str, to_e164: str, body: str) -> str:
        if to_e164 in self.fail_numbers:
            raise RuntimeError(f"simulated delivery failure to {to_e164}")
        self.sent.append((from_e164, to_e164, body))
        log.info("sms[log] %s -> %s: %s", from_e164, to_e164, body)
        return f"log-{len(self.sent)}"


class CarrierSmsProvider:
    """Adapts any `TelephonyProvider` (Telnyx today) to the SMS seam."""

    def __init__(self, carrier: TelephonyProvider) -> None:
        self._carrier = carrier
        self.name = carrier.name

    async def send(self, from_e164: str, to_e164: str, body: str) -> str:
        return await self._carrier.send_sms(from_e164, to_e164, body)


class MessageService:
    def __init__(self, store: CallStore, provider: SmsProvider, from_number: str | None) -> None:
        self.store = store
        self.provider = provider
        self.from_number = from_number

    async def recent(self, tenant_id: str, limit: int = 100) -> list[Message]:
        docs = await self.store.list_docs(KIND, tenant_id, limit)
        return [Message.from_doc(d) for d in docs]

    async def for_call(self, tenant_id: str, call_id: str) -> list[Message]:
        return [m for m in await self.recent(tenant_id, 1000) if m.call_id == call_id]

    async def _already_sent(self, tenant_id: str, call_id: str | None, trigger: SmsTrigger) -> bool:
        if not call_id:
            return False
        return any(
            m.trigger == trigger and m.status == MessageStatus.SENT
            for m in await self.for_call(tenant_id, call_id)
        )

    async def send(
        self,
        tenant_id: str,
        company_id: str,
        to: str,
        body: str,
        *,
        call_id: str | None = None,
        trigger: SmsTrigger = SmsTrigger.CUSTOM,
        scenario_id: str | None = None,
        sender: str | None = None,
    ) -> Message:
        sender = sender or self.from_number
        msg = Message(
            tenant_id=tenant_id,
            company_id=company_id,
            call_id=call_id,
            to=to,
            sender=sender,
            body=body,
            trigger=trigger,
            scenario_id=scenario_id,
            provider=self.provider.name,
        )
        if not sender:
            msg.status = MessageStatus.SKIPPED
            msg.error = "no SMS sender number configured"
        elif not body:
            msg.status = MessageStatus.SKIPPED
            msg.error = "empty message"
        else:
            try:
                msg.provider_ref = await self.provider.send(sender, to, body)
            except Exception as e:
                log.warning("sms send failed to %s: %s", to, e)
                msg.status = MessageStatus.FAILED
                msg.error = str(e)[:300]
        await self.store.put_doc(msg.to_doc())
        return msg

    async def send_scenario(
        self,
        cfg: AssistantConfig,
        trigger: SmsTrigger,
        to: str,
        ctx: dict[str, Any] | None = None,
        *,
        call_id: str | None = None,
    ) -> Message | None:
        """Send the assistant's enabled scenario for `trigger`; None if there isn't one."""
        sc = scenario_for(cfg, trigger)
        if sc is None:
            return None
        if await self._already_sent(cfg.tenant_id, call_id, trigger):
            return None
        body = render_template(sc.template, {**base_context(cfg), **(ctx or {})})
        return await self.send(
            cfg.tenant_id,
            cfg.company_id,
            to,
            body,
            call_id=call_id,
            trigger=trigger,
            scenario_id=sc.id,
        )

    async def handle_request(self, cfg: AssistantConfig, req: SendSmsRequest) -> Message | None:
        if req.trigger and req.trigger != SmsTrigger.CUSTOM:
            return await self.send_scenario(
                cfg, req.trigger, req.to, req.context, call_id=req.call_id
            )
        body = render_template(req.body or "", {**base_context(cfg), **req.context})
        return await self.send(
            cfg.tenant_id,
            cfg.company_id,
            req.to,
            body,
            call_id=req.call_id,
            trigger=SmsTrigger.CUSTOM,
        )

    # -- lifecycle hooks --------------------------------------------------------------------
    async def on_call_ended(self, call: CallRecord, cfg: AssistantConfig) -> Message | None:
        if not call.caller or call.caller.startswith("anonymous"):
            return None
        ctx: dict[str, Any] = {"caller_name": call.extracted.get("name")}
        trigger = SmsTrigger.MISSED_CALL if call_missed(call) else SmsTrigger.AFTER_CALL
        return await self.send_scenario(cfg, trigger, call.caller, ctx, call_id=call.call_id)

    async def on_ticket_created(self, ticket: Ticket, cfg: AssistantConfig) -> Message | None:
        if not ticket.caller_number:
            return None
        ctx = {
            "caller_name": ticket.caller_name,
            "ticket_id": ticket.id,
            "callback_window": ticket.callback_window,
        }
        return await self.send_scenario(
            cfg, SmsTrigger.TICKET_CONFIRMATION, ticket.caller_number, ctx, call_id=ticket.call_id
        )


def call_missed(call: CallRecord) -> bool:
    return call.status == "failed" or call.answered_at is None
