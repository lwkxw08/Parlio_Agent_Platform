"""Glue between call lifecycle and the Phase 5 services (SMS, notifications, SIP admission).

`IntegrationHub.on_event` runs after an event is folded into the store (both the HTTP ingest path
and the Redis consumer call it), `on_postcall` after analysis finishes, `on_ticket` after a ticket
is created. Everything here is best-effort: failures are logged, never raised into call handling.
"""

from __future__ import annotations

import logging

from parlio_api.compliance import ComplianceService
from parlio_api.messaging import MessageService
from parlio_api.notifications import (
    NotificationEvent,
    NotificationService,
    NotifyEvent,
    call_completed_event,
    is_qualified_lead,
)
from parlio_api.observability import Telemetry
from parlio_api.sip import SipService
from parlio_api.store import CallRecord, CallStore, Ticket
from parlio_voice.models import CallEvent, CallEventType

log = logging.getLogger("parlio.api.integrations")


class IntegrationHub:
    def __init__(
        self,
        store: CallStore,
        sms: MessageService,
        notifications: NotificationService,
        sip: SipService,
        telemetry: Telemetry | None = None,
        compliance: ComplianceService | None = None,
    ) -> None:
        self.store = store
        self.sms = sms
        self.notifications = notifications
        self.sip = sip
        self.telemetry = telemetry
        self.compliance = compliance

    async def on_event(self, ev: CallEvent) -> None:
        if self.telemetry is not None:
            self.telemetry.on_event(ev)
        if ev.type not in (CallEventType.CALL_ENDED, CallEventType.CALL_FAILED):
            return
        self.sip.release(ev.call_id)
        if self.compliance is not None:
            try:
                await self.compliance.redact_call_on_close(ev.tenant_id, ev.call_id)
            except Exception:
                log.warning("redact-on-write failed for %s", ev.call_id, exc_info=True)
        try:
            call = await self.store.get_call(ev.call_id)
            cfg = await self.store.get_assistant(ev.assistant_id)
            if call is not None and cfg is not None:
                await self.sms.on_call_ended(call, cfg)
        except Exception:
            log.warning("post-call SMS failed for %s", ev.call_id, exc_info=True)

    async def on_postcall(self, call: CallRecord) -> None:
        try:
            cfg = await self.store.get_assistant(call.assistant_id)
            ev = call_completed_event(call, cfg.business_name if cfg else "")
            await self.notifications.dispatch(ev)
            if is_qualified_lead(call):
                await self.notifications.dispatch(
                    ev.model_copy(update={"event": NotifyEvent.LEAD_QUALIFIED, "qualified": True})
                )
        except Exception:
            log.warning("post-call notifications failed for %s", call.call_id, exc_info=True)
        if self.compliance is not None:
            try:
                await self.compliance.redact_call_on_close(call.tenant_id, call.call_id)
            except Exception:
                log.warning("redact-on-write failed for %s", call.call_id, exc_info=True)

    async def on_ticket(self, ticket: Ticket) -> None:
        try:
            cfg = None
            if ticket.call_id:
                call = await self.store.get_call(ticket.call_id)
                if call is not None:
                    cfg = await self.store.get_assistant(call.assistant_id)
            if cfg is None:
                cfgs = await self.store.list_assistants(ticket.tenant_id)
                cfg = cfgs[0] if cfgs else None
            if cfg is not None:
                await self.sms.on_ticket_created(ticket, cfg)
        except Exception:
            log.warning("ticket SMS failed for %s", ticket.id, exc_info=True)

    async def notify(self, ev: NotificationEvent) -> None:
        try:
            await self.notifications.dispatch(ev)
        except Exception:
            log.warning("notification dispatch failed", exc_info=True)
