"""Team notifications: per-tenant rules route platform events to email / SMS / Slack / webhook.

A `NotificationRule` says "send <events> to <channel:target>", optionally only for qualified
leads or particular departments. `NotificationService.dispatch()` fans an event out to matching
rules, records a `Notification` per attempt (sent/failed/skipped) and never raises - alerting must
not break call processing. `RuleNotifier` adapts this to the Phase 3 `tickets.Notifier` seam.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from parlio_api.messaging import MessageService, MessageStatus
from parlio_api.store import CallRecord, CallStore, TenantDoc
from parlio_voice.models import SmsTrigger

log = logging.getLogger("parlio.api.notifications")

RULE_KIND = "notification_rule"
LOG_KIND = "notification"


class Channel(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    SLACK = "slack"
    WEBHOOK = "webhook"


class NotifyEvent(StrEnum):
    TICKET_CREATED = "ticket.created"
    TICKET_URGENT = "ticket.urgent"
    SLA_BREACHED = "ticket.sla_breached"
    CALL_MISSED = "call.missed"
    CALL_ESCALATED = "call.escalated"
    CALL_COMPLETED = "call.completed"
    LEAD_QUALIFIED = "lead.qualified"
    BOOKING_CREATED = "booking.created"
    SIP_REGISTRATION = "sip.registration_changed"
    APPROVAL_REQUESTED = "approval.requested"


class NotificationRule(BaseModel):
    id: str = Field(default_factory=lambda: f"nr-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
    name: str = "Team alerts"
    channel: Channel
    target: str = Field(min_length=3, description="email address, E.164 number or webhook URL")
    events: list[NotifyEvent] = Field(default_factory=lambda: [NotifyEvent.TICKET_URGENT])
    enabled: bool = True
    qualified_only: bool = Field(
        default=False, description="For call events: only notify when the call is a qualified lead."
    )
    departments: list[str] = Field(default_factory=list, description="Empty = all departments")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def matches(self, ev: NotificationEvent) -> bool:
        if not self.enabled or ev.event not in self.events:
            return False
        if self.departments and (ev.department or "").lower() not in {
            d.lower() for d in self.departments
        }:
            return False
        return not (self.qualified_only and ev.event.startswith("call.") and not ev.qualified)

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=RULE_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class NotificationEvent(BaseModel):
    tenant_id: str
    company_id: str | None = None
    event: NotifyEvent
    title: str
    body: str
    department: str | None = None
    qualified: bool = False
    context: dict[str, Any] = Field(default_factory=dict)


class NotificationStatus(StrEnum):
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class Notification(BaseModel):
    id: str = Field(default_factory=lambda: f"nt-{uuid4().hex[:10]}")
    tenant_id: str
    rule_id: str | None = None
    channel: Channel
    target: str
    event: NotifyEvent
    title: str
    body: str
    status: NotificationStatus = NotificationStatus.SENT
    error: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=LOG_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


# -- channel senders -------------------------------------------------------------------------


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> str: ...


class LogEmailSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, to: str, subject: str, body: str) -> str:
        self.sent.append((to, subject, body))
        log.info("email[log] %s: %s", to, subject)
        return f"log-{len(self.sent)}"


class ResendEmailSender:
    """Transactional email via Resend (https://resend.com/docs/api-reference/emails/send-email)."""

    def __init__(self, api_key: str, sender: str, client: httpx.AsyncClient | None = None) -> None:
        self._from = sender
        self._http = client or httpx.AsyncClient(
            base_url="https://api.resend.com",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )

    async def send(self, to: str, subject: str, body: str) -> str:
        r = await self._http.post(
            "/emails", json={"from": self._from, "to": [to], "subject": subject, "text": body}
        )
        r.raise_for_status()
        return str(r.json().get("id", ""))

    async def aclose(self) -> None:
        await self._http.aclose()


def slack_payload(title: str, body: str, ev: NotificationEvent | None) -> dict[str, Any]:
    text = f"*{title}*\n{body}"
    payload: dict[str, Any] = {"text": text}
    if ev is not None:
        payload["blocks"] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"`{ev.event}` | dept: {ev.department or 'any'}"}
                ],
            },
        ]
    return payload


# -- service ---------------------------------------------------------------------------------


class NotificationService:
    def __init__(
        self,
        store: CallStore,
        email: EmailSender,
        sms: MessageService | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        fallback_webhook_url: str | None = None,
    ) -> None:
        self.store = store
        self.email = email
        self.sms = sms
        self._http = http or httpx.AsyncClient(timeout=5.0)
        self.fallback_webhook_url = fallback_webhook_url

    async def aclose(self) -> None:
        await self._http.aclose()

    # rules
    async def rules(self, tenant_id: str) -> list[NotificationRule]:
        docs = await self.store.list_docs(RULE_KIND, tenant_id)
        return sorted(
            (NotificationRule.model_validate(d.data) for d in docs), key=lambda r: r.created_at
        )

    async def put_rule(self, rule: NotificationRule) -> NotificationRule:
        await self.store.put_doc(rule.to_doc())
        return rule

    async def get_rule(self, tenant_id: str, rule_id: str) -> NotificationRule | None:
        d = await self.store.get_doc(RULE_KIND, rule_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return NotificationRule.model_validate(d.data)

    async def delete_rule(self, tenant_id: str, rule_id: str) -> bool:
        if await self.get_rule(tenant_id, rule_id) is None:
            return False
        return await self.store.delete_doc(RULE_KIND, rule_id)

    async def log(self, tenant_id: str, limit: int = 100) -> list[Notification]:
        docs = await self.store.list_docs(LOG_KIND, tenant_id, limit)
        return [Notification.model_validate(d.data) for d in docs]

    # delivery
    async def dispatch(self, ev: NotificationEvent) -> list[Notification]:
        rules = [r for r in await self.rules(ev.tenant_id) if r.matches(ev)]
        out: list[Notification] = []
        for r in rules:
            out.append(await self.deliver(r, ev))
        if not rules and self.fallback_webhook_url:
            out.append(
                await self.deliver(
                    NotificationRule(
                        id="platform-fallback",
                        tenant_id=ev.tenant_id,
                        company_id=ev.company_id or "",
                        channel=Channel.WEBHOOK,
                        target=self.fallback_webhook_url,
                        events=[ev.event],
                    ),
                    ev,
                )
            )
        return out

    async def test_rule(self, rule: NotificationRule) -> Notification:
        ev = NotificationEvent(
            tenant_id=rule.tenant_id,
            company_id=rule.company_id,
            event=rule.events[0] if rule.events else NotifyEvent.TICKET_CREATED,
            title="Parlio test notification",
            body=f"This confirms {rule.channel} alerts to {rule.target} are working.",
        )
        return await self.deliver(rule, ev)

    async def deliver(self, rule: NotificationRule, ev: NotificationEvent) -> Notification:
        n = Notification(
            tenant_id=ev.tenant_id,
            rule_id=rule.id,
            channel=rule.channel,
            target=rule.target,
            event=ev.event,
            title=ev.title,
            body=ev.body,
            context=ev.context,
        )
        try:
            match rule.channel:
                case Channel.EMAIL:
                    await self.email.send(rule.target, ev.title, ev.body)
                case Channel.SMS:
                    if self.sms is None:
                        n.status = NotificationStatus.SKIPPED
                        n.error = "SMS not configured"
                    else:
                        m = await self.sms.send(
                            ev.tenant_id,
                            ev.company_id or rule.company_id,
                            rule.target,
                            f"{ev.title}: {ev.body}"[:480],
                            call_id=ev.context.get("call_id"),
                            trigger=SmsTrigger.CUSTOM,
                        )
                        if m.status != MessageStatus.SENT:
                            n.status = NotificationStatus(m.status.value)
                            n.error = m.error
                case Channel.SLACK:
                    r = await self._http.post(
                        rule.target, json=slack_payload(ev.title, ev.body, ev)
                    )
                    r.raise_for_status()
                case Channel.WEBHOOK:
                    r = await self._http.post(
                        rule.target,
                        json={
                            "text": f"*{ev.title}*\n{ev.body}",
                            "event": ev.event,
                            "tenant_id": ev.tenant_id,
                            "title": ev.title,
                            "body": ev.body,
                            "context": ev.context,
                        },
                    )
                    r.raise_for_status()
        except Exception as e:
            log.warning("notification via %s to %s failed: %s", rule.channel, rule.target, e)
            n.status = NotificationStatus.FAILED
            n.error = str(e)[:300]
        if rule.id != "platform-fallback":
            await self.store.put_doc(n.to_doc())
        return n


class RuleNotifier:
    """`tickets.Notifier` implementation that routes ticket alerts through tenant rules."""

    LEVELS = {
        "urgent": NotifyEvent.TICKET_URGENT,
        "escalation": NotifyEvent.SLA_BREACHED,
    }

    def __init__(self, svc: NotificationService) -> None:
        self._svc = svc
        self.sent: list[tuple[str, str, str, str]] = []

    async def send(self, tenant_id: str, level: str, title: str, body: str) -> None:
        self.sent.append((tenant_id, level, title, body))
        event = self.LEVELS.get(level, NotifyEvent.TICKET_CREATED)
        ev = NotificationEvent(tenant_id=tenant_id, event=event, title=title, body=body)
        delivered = await self._svc.dispatch(ev)
        if event == NotifyEvent.TICKET_URGENT:
            hit = {n.rule_id for n in delivered}
            created = ev.model_copy(update={"event": NotifyEvent.TICKET_CREATED})
            for r in await self._svc.rules(tenant_id):
                if r.id not in hit and r.matches(created):
                    await self._svc.deliver(r, created)


def is_qualified_lead(call: CallRecord) -> bool:
    """A new caller who stayed on the line and left usable details (name/phone/email/reason)."""
    if call.status == "failed" or call.answered_at is None:
        return False
    if call.caller_type not in ("new", "prospect", None):
        return False
    details = {k: v for k, v in call.extracted.items() if v}
    long_enough = (call.duration_s or 0) >= 30
    return (bool(details) and long_enough) or bool(call.ticket_ids)


def call_completed_event(call: CallRecord, business_name: str) -> NotificationEvent:
    missed = call.status == "failed" or call.answered_at is None
    who = call.extracted.get("name") or call.caller or "Unknown caller"
    qualified = is_qualified_lead(call)
    summary = call.summary or call.end_reason or ("Missed call" if missed else "Call completed")
    dur = f" ({int(call.duration_s)}s)" if call.duration_s else ""
    return NotificationEvent(
        tenant_id=call.tenant_id,
        company_id=call.company_id,
        event=NotifyEvent.CALL_MISSED if missed else NotifyEvent.CALL_COMPLETED,
        title=(
            f"Missed call from {who}"
            if missed
            else f"{'Qualified lead' if qualified else 'Call'}: {who}{dur}"
        ),
        body=f"{summary} | {business_name}",
        qualified=qualified,
        context={"call_id": call.call_id, "caller": call.caller, "caller_type": call.caller_type},
    )
