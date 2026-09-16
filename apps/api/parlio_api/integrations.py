"""Glue between call lifecycle and the Phase 5 services (SMS, notifications, SIP admission).

`IntegrationHub.on_event` runs after an event is folded into the store (both the HTTP ingest path
and the Redis consumer call it), `on_postcall` after analysis finishes, `on_ticket` after a ticket
is created. Everything here is best-effort: failures are logged, never raised into call handling.
"""

from __future__ import annotations

import logging
from contextlib import suppress

from parlio_api.calendar import Booking
from parlio_api.compliance import ComplianceService
from parlio_api.connectors import (
    ConnectorService,
    payload_from_booking,
    payload_from_call,
    payload_from_ticket,
)
from parlio_api.inbox import InboxService
from parlio_api.live import LiveCallHub
from parlio_api.messaging import MessageService
from parlio_api.notifications import (
    NotificationEvent,
    NotificationService,
    NotifyEvent,
    call_completed_event,
    is_qualified_lead,
)
from parlio_api.observability import Telemetry
from parlio_api.outbound import OutboundService
from parlio_api.qa import QAService
from parlio_api.reminders import ReminderService
from parlio_api.sip import SipService
from parlio_api.store import CallRecord, CallStore, Ticket
from parlio_voice.models import AssistantConfig, CallEvent, CallEventType

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
        connectors: ConnectorService | None = None,
        outbound: OutboundService | None = None,
        live: LiveCallHub | None = None,
    ) -> None:
        self.store = store
        self.sms = sms
        self.notifications = notifications
        self.sip = sip
        self.telemetry = telemetry
        self.compliance = compliance
        self.connectors = connectors
        self.outbound = outbound
        self.live = live
        self.inbox: InboxService | None = None
        self.qa: QAService | None = None
        self.reminders: ReminderService | None = None

    async def business_name_for(self, tenant_id: str, assistant_id: str | None = None) -> str:
        cfg = await self.store.get_assistant(assistant_id) if assistant_id else None
        if cfg is None:
            cfgs = await self.store.list_assistants(tenant_id)
            cfg = cfgs[0] if cfgs else None
        return cfg.business_name if cfg else ""

    async def on_event(self, ev: CallEvent) -> None:
        if self.telemetry is not None:
            self.telemetry.on_event(ev)
        if self.live is not None:
            self.live.on_event(ev)
        if self.outbound is not None and ev.type == CallEventType.CALL_STARTED:
            with suppress(Exception):
                await self.outbound.on_call_started(ev.call_id)
        if ev.type not in (CallEventType.CALL_ENDED, CallEventType.CALL_FAILED):
            return
        if self.outbound is not None:
            try:
                call = await self.store.get_call(ev.call_id)
                if call is not None and call.direction == "outbound":
                    await self.outbound.on_call_ended(call)
            except Exception:
                log.warning("outbound outcome failed for %s", ev.call_id, exc_info=True)
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
        if self.inbox is not None:
            try:
                await self.inbox.on_call_ended(call)
            except Exception:
                log.warning("inbox call thread failed for %s", call.call_id, exc_info=True)
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
        if self.connectors is not None and call.kind != "blocked":
            try:
                name = await self.business_name_for(call.tenant_id, call.assistant_id)
                await self.connectors.dispatch(
                    payload_from_call(
                        call, name, is_qualified_lead(call), self.connectors.public_url
                    )
                )
            except Exception:
                log.warning("connector sync failed for %s", call.call_id, exc_info=True)
        if self.qa is not None and call.kind != "blocked":
            try:
                await self.qa.score_call(call)
            except Exception:
                log.warning("QA scoring failed for %s", call.call_id, exc_info=True)
        if self.compliance is not None:
            try:
                await self.compliance.redact_call_on_close(call.tenant_id, call.call_id)
            except Exception:
                log.warning("redact-on-write failed for %s", call.call_id, exc_info=True)

    async def on_ticket(self, ticket: Ticket) -> None:
        cfg: AssistantConfig | None = None
        try:
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
        if self.connectors is not None:
            try:
                name = cfg.business_name if cfg else await self.business_name_for(ticket.tenant_id)
                await self.connectors.dispatch(payload_from_ticket(ticket, name))
            except Exception:
                log.warning("connector sync failed for ticket %s", ticket.id, exc_info=True)
        if self.outbound is not None:
            try:
                await self.outbound.on_ticket_created(ticket)
            except Exception:
                log.warning("ticket callback scheduling failed for %s", ticket.id, exc_info=True)

    async def on_booking(self, booking: Booking) -> None:
        if self.reminders is not None:
            try:
                await self.reminders.on_booking(booking)
            except Exception:
                log.warning("SMS reminder scheduling failed for %s", booking.id, exc_info=True)
        if self.outbound is not None:
            try:
                await self.outbound.on_booking(
                    tenant_id=booking.tenant_id,
                    booking_id=booking.id,
                    phone=booking.phone,
                    name=booking.name,
                    start=booking.start,
                )
            except Exception:
                log.warning("reminder scheduling failed for %s", booking.id, exc_info=True)
        if self.connectors is None:
            return
        try:
            name = await self.business_name_for(booking.tenant_id)
            await self.connectors.dispatch(payload_from_booking(booking, name))
        except Exception:
            log.warning("connector sync failed for booking %s", booking.id, exc_info=True)

    async def notify(self, ev: NotificationEvent) -> None:
        try:
            await self.notifications.dispatch(ev)
        except Exception:
            log.warning("notification dispatch failed", exc_info=True)
