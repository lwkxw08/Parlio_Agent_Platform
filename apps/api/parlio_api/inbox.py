"""Phase 11: omnichannel shared inbox.

One ``Thread`` per contact identity and channel (SMS number, WhatsApp number, web-chat visitor,
call summaries) holding ``InboxMessage``s. Inbound text is answered by the same assistant brain
that runs on calls (``TextAgent`` built from the ``AssistantConfig`` knowledge sections) until a
human joins the thread, after which the AI stays quiet until handed back. Team features: assign,
internal notes, canned replies, unread + SLA states, live updates through ``LiveCallHub``.

Channel delivery is behind ``ChannelSender``: SMS reuses ``MessageService`` (Telnyx), WhatsApp
uses the Meta Cloud API (simulated when no access token), web chat is stored and polled by the
widget.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from parlio_api.billing import WEB_CALLER_PREFIX
from parlio_api.live import LiveCallHub, LiveMessage
from parlio_api.messaging import MessageService, MessageStatus
from parlio_api.notifications import NotificationEvent, NotificationService, NotifyEvent
from parlio_api.store import CallRecord, CallStore, TenantDoc, Ticket
from parlio_voice.models import AssistantConfig, TicketIntake, TicketPriority

log = logging.getLogger("parlio.api.inbox")

THREAD_KIND = "inbox_thread"
MESSAGE_KIND = "inbox_message"
CANNED_KIND = "canned_reply"
WIDGET_KIND = "chat_widget"
WHATSAPP_KIND = "whatsapp_account"

MAX_HISTORY = 40
DEFAULT_SLA_MINUTES = 15


class Channel(StrEnum):
    CALL = "call"
    VOICEMAIL = "voicemail"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    WEBCHAT = "webchat"


TEXT_CHANNELS = {Channel.SMS, Channel.WHATSAPP, Channel.WEBCHAT}


class ThreadStatus(StrEnum):
    OPEN = "open"
    WAITING = "waiting"  # human requested / AI handed off, nobody replied yet
    CLOSED = "closed"


class Direction(StrEnum):
    IN = "in"  # contact -> business
    OUT = "out"  # business (AI or human) -> contact
    NOTE = "note"  # internal, never delivered


class Author(StrEnum):
    CONTACT = "contact"
    AI = "ai"
    AGENT = "agent"
    SYSTEM = "system"


class Thread(BaseModel):
    id: str = Field(default_factory=lambda: f"th-{uuid4().hex[:10]}")
    tenant_id: str
    company_id: str
    channel: Channel
    identity: str  # E.164 for sms/whatsapp/call, visitor id for webchat
    contact_id: str | None = None
    contact_name: str | None = None
    subject: str | None = None
    status: ThreadStatus = ThreadStatus.OPEN
    assigned_to: str | None = None
    ai_enabled: bool = True
    handoff_department: str | None = None
    callback_ticket_id: str | None = None
    unread: int = 0
    message_count: int = 0
    last_preview: str = ""
    last_direction: Direction | None = None
    last_message_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sla_due_at: datetime | None = None
    sla_breached: bool = False
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=THREAD_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )

    @classmethod
    def from_doc(cls, d: TenantDoc) -> Thread:
        return cls.model_validate(d.data)


class InboxMessage(BaseModel):
    id: str = Field(default_factory=lambda: f"im-{uuid4().hex[:10]}")
    tenant_id: str
    thread_id: str
    channel: Channel
    direction: Direction
    author: Author
    author_name: str | None = None  # agent email / assistant name
    text: str
    call_id: str | None = None
    ticket_id: str | None = None
    handoff: bool = False
    clarifying: bool = False  # AI asked what the handoff is about before connecting
    status: str = "sent"  # sent | failed | skipped | received
    error: str | None = None
    provider_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=MESSAGE_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )

    @classmethod
    def from_doc(cls, d: TenantDoc) -> InboxMessage:
        return cls.model_validate(d.data)


class CannedReply(BaseModel):
    id: str = Field(default_factory=lambda: f"cr-{uuid4().hex[:8]}")
    tenant_id: str
    title: str = Field(min_length=1, max_length=80)
    shortcut: str | None = Field(default=None, max_length=24)
    body: str = Field(min_length=1, max_length=1600)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=CANNED_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class ChatWidget(BaseModel):
    """Per-tenant web chat widget settings; ``token`` is the public embed key."""

    id: str = Field(default_factory=lambda: f"cw-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
    token: str = Field(default_factory=lambda: secrets.token_urlsafe(18))
    enabled: bool = True
    title: str = "Chat with us"
    greeting: str = "Hi! How can we help today?"
    colour: str = "#3b5bdb"
    allowed_origins: list[str] = Field(default_factory=list)
    voice_enabled: bool = True  # "Talk to us" browser-voice button (Phase 11b)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=WIDGET_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )

    def public(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "greeting": self.greeting,
            "colour": self.colour,
            "enabled": self.enabled,
            "voice_enabled": self.voice_enabled,
        }


class WhatsAppAccount(BaseModel):
    """Meta WhatsApp Business Cloud API binding for a tenant."""

    id: str = Field(default_factory=lambda: f"wa-{uuid4().hex[:8]}")
    tenant_id: str
    company_id: str
    phone_number_id: str = Field(min_length=3)
    display_number: str | None = None
    access_token: str | None = Field(default=None, exclude=True)
    verify_token: str = Field(default_factory=lambda: secrets.token_urlsafe(12))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=WHATSAPP_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data={**self.model_dump(mode="json"), "access_token": self.access_token},
            created_at=self.created_at,
        )


# -- assistant brain for text -----------------------------------------------------------------


class AgentTurn(BaseModel):
    reply: str
    handoff: bool = False  # human needed: pause AI, mark thread waiting, notify team
    clarifying: bool = False  # asked the customer what the handoff is about; AI stays on
    department: str | None = None
    ticket: TicketIntake | None = None


def _departments_section(cfg: AssistantConfig) -> str:
    depts = cfg.transfer.departments()
    if not cfg.transfer.enabled or not depts:
        return "There are no departments to transfer to; a handoff goes to the general team."
    now = datetime.now(UTC)
    lines = []
    for d in depts:
        open_now = bool(cfg.transfer.candidates(d, now))
        note = cfg.transfer.department_notes.get(d)
        lines.append(
            f"- {d}: {note + '; ' if note else ''}"
            f"{'available now' if open_now else 'closed now - callback'}"
        )
    return "Departments a human handoff can go to:\n" + "\n".join(lines)


class TextAgent(Protocol):
    name: str

    async def respond(
        self, cfg: AssistantConfig, thread: Thread, history: list[InboxMessage]
    ) -> AgentTurn: ...


_WORD = re.compile(r"[a-z0-9']+")
_HUMAN = (
    "speak to someone",
    "speak to a human",
    "speak to a person",
    "talk to a human",
    "talk to a person",
    "talk to someone",
    "real person",
    "human",
    "transfer",
    "an agent",
    "a representative",
    "call me",
)
_HOURS = ("open", "opening", "hours", "close", "closing")
_CLOSING = ("thanks", "thank you", "great", "cheers", "ok", "okay", "perfect", "bye", "brilliant")
_BOOK = ("book", "appointment", "booking", "schedule", "slot")
_STOP = {"the", "a", "an", "is", "are", "do", "you", "i", "to", "of", "and", "what", "how", "can"}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP}


def _stems(text: str) -> set[str]:
    return {t.rstrip("s") for t in _tokens(text)}


class RuleTextAgent:
    """Offline brain: FAQ overlap match, opening hours, booking link, otherwise take a message."""

    name = "rules"

    def __init__(self, booking_url: Callable[[str], Awaitable[str | None]] | None = None) -> None:
        self._booking_url = booking_url

    async def respond(
        self, cfg: AssistantConfig, thread: Thread, history: list[InboxMessage]
    ) -> AgentTurn:
        last = next((m for m in reversed(history) if m.direction == Direction.IN), None)
        text = (last.text if last else "").strip()
        low = text.lower()
        inbound = [m for m in history if m.direction == Direction.IN]
        greet = "" if len(inbound) > 1 else f"Hi, this is {cfg.name} from {cfg.business_name}. "

        if len(inbound) > 1 and len(_tokens(text)) <= 4 and any(k in low for k in _CLOSING):
            return AgentTurn(reply="You're welcome — anything else, just message us here.")

        prev_ai = next((m for m in reversed(history) if m.direction == Direction.OUT), None)
        if prev_ai is not None and prev_ai.clarifying:
            dept = _match_department(cfg, text)
            return AgentTurn(
                reply=f"Thanks — I'm connecting you to {dept or 'the team'} now. It may take a "
                "couple of minutes for someone to pick up; please stay in the chat.",
                handoff=True,
                department=dept,
            )
        if any(k in low for k in _HUMAN):
            if len(cfg.transfer.departments()) > 1:
                return AgentTurn(
                    reply=f"{greet}Of course. So I can get you to the right team, what is it "
                    "about?",
                    clarifying=True,
                )
            return AgentTurn(
                reply=f"{greet}Of course — I'm connecting you to the team now. It may take a "
                "couple of minutes for someone to pick up; please stay in the chat.",
                handoff=True,
            )

        best, score = None, 0
        q = _tokens(text)
        for f in cfg.faqs:
            if not f.enabled:
                continue
            s = len(q & _tokens(f.question + " " + f.answer))
            if s > score:
                best, score = f, s
        if best is not None and score >= 2:
            return AgentTurn(reply=f"{greet}{best.answer}")

        if any(k in low for k in _HOURS):
            return AgentTurn(reply=f"{greet}{_hours_line(cfg)}")

        if any(k in low for k in _BOOK):
            url = await self._booking_url(cfg.tenant_id) if self._booking_url else None
            if url:
                return AgentTurn(reply=f"{greet}You can book directly here: {url}")
            return AgentTurn(
                reply=f"{greet}I've passed your booking request to the team — they'll confirm "
                "a time with you here shortly.",
                handoff=True,
                ticket=_message_ticket(thread, text, "booking"),
            )

        if len(inbound) <= 1:
            return AgentTurn(
                reply=f"{greet}Thanks for your message — I've passed it to the team and someone "
                "will get back to you here. Is there anything else I can help with?",
                handoff=True,
                ticket=_message_ticket(thread, text, None),
            )
        return AgentTurn(
            reply="Noted, thank you — I've added that to your message for the team.",
            handoff=True,
        )


def display_name(email: str) -> str:
    local = email.split("@", 1)[0]
    return " ".join(p.capitalize() for p in re.split(r"[._-]+", local) if p) or email


def _match_department(cfg: AssistantConfig, text: str) -> str | None:
    """Pick the department whose name/description overlaps most with the customer's words."""
    q = _stems(text)
    best, score = None, 0
    for d in cfg.transfer.departments():
        s = len(q & _stems(f"{d} {cfg.transfer.department_notes.get(d, '')}"))
        if s > score:
            best, score = d, s
    return best


def _handoff_ticket(thread: Thread) -> TicketIntake:
    return TicketIntake(
        caller_number=thread.identity if thread.channel != Channel.WEBCHAT else None,
        caller_name=thread.contact_name,
        reason=f"{thread.channel} customer asked for a person and nobody picked up: "
        f"{thread.last_preview}",
        category=thread.handoff_department or "callback",
        priority=TicketPriority.HIGH,
        source="ai_intake",
    )


def _message_ticket(thread: Thread, text: str, category: str | None) -> TicketIntake:
    return TicketIntake(
        caller_number=thread.identity if thread.channel != Channel.WEBCHAT else None,
        caller_name=thread.contact_name,
        reason=f"{thread.channel} message: “{text[:300]}”",
        category=category,
        priority=TicketPriority.NORMAL,
        source="ai_intake",
    )


def _hours_line(cfg: AssistantConfig) -> str:
    if cfg.hours.always:
        return "We're open 24 hours."
    parts = [
        f"{d.title()} {h.open:%H:%M}-{h.close:%H:%M}"
        for d, h in cfg.hours.hours.items()
        if h is not None
    ]
    state = "open now" if cfg.is_open() else "closed at the moment"
    return f"We're {state}. Opening hours: {', '.join(parts) or 'not set'}."


class OpenAITextAgent:
    """JSON-mode chat completion using the same Studio knowledge sections as the voice prompt."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
        fallback: TextAgent | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
        )
        self._model = model
        self._fallback = fallback or RuleTextAgent()

    async def respond(
        self, cfg: AssistantConfig, thread: Thread, history: list[InboxMessage]
    ) -> AgentTurn:
        already_handed_off = any(
            m.direction == Direction.OUT and m.author == Author.AI and m.handoff for m in history
        )
        last_ai = next(
            (
                m
                for m in reversed(history)
                if m.direction == Direction.OUT and m.author == Author.AI
            ),
            None,
        )
        just_asked_topic = last_ai is not None and last_ai.clarifying
        multi_dept = len(cfg.transfer.departments()) > 1
        system = "\n".join(
            [
                cfg.rendered_instructions(),
                f"You are {cfg.name}, replying by {thread.channel} on behalf of "
                f"{cfg.business_name}. Keep replies short (1-3 sentences), plain text, "
                "no markdown.",
                *cfg.knowledge_sections(),
                _departments_section(cfg),
                'Return JSON: {"reply": <text to send>, "handoff": <true only when you are '
                'connecting them to a human NOW>, "clarifying": <true if you are asking what '
                'the handoff is about before connecting>, "department": <department name or '
                'null>, "ticket": null | {"reason": <what the customer needs>, "category": '
                '<string|null>, "priority": "low"|"normal"|"high"|"urgent"}}.',
                "Handoff policy: if the customer asks for a person, a human, a manager, or to be "
                "transferred"
                + (
                    " and you do not yet know what it is about, ask ONE short question about "
                    "what they need (clarifying=true, handoff=false) and wait for the answer. "
                    "Once you know the topic"
                    if multi_dept
                    else ""
                )
                + ", set handoff=true, choose the department that matches their topic "
                "(e.g. invoices/payments -> accounts) and tell them you are connecting them to "
                "that team and that it may take a couple of minutes for someone to pick up. "
                "Once handoff is true, keep it true on every later turn - never switch to a "
                "ticket; a callback ticket is raised automatically if nobody picks up. Only set "
                "a ticket yourself when no handoff is wanted and you cannot fully resolve the "
                "request. Never invent facts.",
                (
                    "A handoff to a human is already in progress on this conversation; "
                    "acknowledge briefly, keep handoff=true and do not raise a ticket."
                    if already_handed_off
                    else ""
                ),
                (
                    "You already asked what the handoff is about; the customer has now answered. "
                    "Do not ask again: set handoff=true with the best-matching department (or "
                    "null for the general team) and tell them you are connecting them."
                    if just_asked_topic
                    else ""
                ),
            ]
        )
        msgs: list[dict[str, str]] = [{"role": "system", "content": system}]
        for m in history[-MAX_HISTORY:]:
            if m.direction == Direction.NOTE:
                continue
            role = "user" if m.direction == Direction.IN else "assistant"
            msgs.append({"role": role, "content": m.text})
        try:
            r = await self._client.post(
                "/chat/completions",
                json={
                    "model": cfg.llm_model or self._model,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                    "messages": msgs,
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            raw = _LlmTurn.model_validate_json(content)
            ticket: TicketIntake | None = None
            handoff = raw.handoff or already_handed_off or just_asked_topic
            clarifying = raw.clarifying and not handoff
            if (
                handoff
                and not already_handed_off
                and not just_asked_topic
                and multi_dept
                and raw.department is None
                and raw.reply.rstrip().endswith("?")
            ):
                # Asked a question and paused itself in the same turn: keep the AI on to hear
                # the answer, then connect on the next turn.
                handoff, clarifying = False, True
            if raw.ticket is not None and not handoff and not clarifying:
                ticket = TicketIntake(
                    caller_number=thread.identity if thread.channel != Channel.WEBCHAT else None,
                    caller_name=thread.contact_name,
                    reason=raw.ticket.reason,
                    category=raw.ticket.category,
                    priority=raw.ticket.priority,
                    source="ai_intake",
                )
            return AgentTurn(
                reply=raw.reply.strip(),
                handoff=handoff,
                clarifying=clarifying,
                department=raw.department,
                ticket=ticket,
            )
        except Exception:
            log.warning("LLM text reply failed for %s; using rules", thread.id, exc_info=True)
            return await self._fallback.respond(cfg, thread, history)


class _LlmTicket(BaseModel):
    reason: str
    category: str | None = None
    priority: TicketPriority = TicketPriority.NORMAL


class _LlmTurn(BaseModel):
    reply: str
    handoff: bool = False
    clarifying: bool = False
    department: str | None = None
    ticket: _LlmTicket | None = None


# -- channel delivery -------------------------------------------------------------------------


class Delivery(BaseModel):
    status: str  # sent | failed | skipped
    provider_ref: str | None = None
    error: str | None = None


class ChannelSender(Protocol):
    async def send(self, thread: Thread, text: str) -> Delivery: ...


class SmsSender:
    def __init__(self, sms: MessageService) -> None:
        self._sms = sms

    async def send(self, thread: Thread, text: str) -> Delivery:
        m = await self._sms.send(thread.tenant_id, thread.company_id, thread.identity, text)
        status = {
            MessageStatus.SENT: "sent",
            MessageStatus.FAILED: "failed",
            MessageStatus.SKIPPED: "skipped",
        }[m.status]
        return Delivery(status=status, provider_ref=m.provider_ref, error=m.error)


class WhatsAppSender:
    """Meta Cloud API ``/{phone_number_id}/messages``; logs instead when no token is stored."""

    GRAPH = "https://graph.facebook.com/v20.0"

    def __init__(
        self,
        accounts: Callable[[str], Awaitable[WhatsAppAccount | None]],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._accounts = accounts
        self._client = client or httpx.AsyncClient(timeout=15)
        self.sent: list[tuple[str, str]] = []

    async def send(self, thread: Thread, text: str) -> Delivery:
        acct = await self._accounts(thread.tenant_id)
        if acct is None:
            return Delivery(status="skipped", error="no WhatsApp account connected")
        if not acct.access_token:
            self.sent.append((thread.identity, text))
            log.info("whatsapp[log] -> %s: %s", thread.identity, text)
            return Delivery(status="sent", provider_ref=f"log-{len(self.sent)}")
        try:
            r = await self._client.post(
                f"{self.GRAPH}/{acct.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {acct.access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": thread.identity.lstrip("+"),
                    "type": "text",
                    "text": {"body": text},
                },
            )
            r.raise_for_status()
            ref = str((r.json().get("messages") or [{}])[0].get("id") or "")
            return Delivery(status="sent", provider_ref=ref or None)
        except Exception as e:
            return Delivery(status="failed", error=str(e)[:300])


class StoreOnlySender:
    """Web chat: the message is persisted and the widget polls for it."""

    async def send(self, thread: Thread, text: str) -> Delivery:
        return Delivery(status="sent")


# -- inbound webhook helpers --------------------------------------------------------------------


def verify_meta_signature(app_secret: str | None, body: bytes, header: str | None) -> bool:
    """``X-Hub-Signature-256: sha256=<hmac>`` from Meta; accept everything when no secret set."""
    if not app_secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header[len("sha256=") :])


class InboundText(BaseModel):
    channel: Channel
    identity: str
    text: str
    to: str | None = None  # dialled number / phone_number_id used to find the tenant
    name: str | None = None
    provider_ref: str | None = None


def parse_telnyx_sms(payload: dict[str, Any]) -> InboundText | None:
    """Telnyx ``message.received`` webhook -> InboundText (None for other event types)."""
    data = payload.get("data") or {}
    if data.get("event_type") != "message.received":
        return None
    p = data.get("payload") or {}
    frm = (p.get("from") or {}).get("phone_number")
    to_list = p.get("to") or []
    to = to_list[0].get("phone_number") if to_list else None
    text = p.get("text") or ""
    if not frm or not text:
        return None
    return InboundText(
        channel=Channel.SMS, identity=frm, text=text, to=to, provider_ref=p.get("id")
    )


def parse_meta_whatsapp(payload: dict[str, Any]) -> list[InboundText]:
    """Meta WhatsApp webhook -> one InboundText per text message."""
    out: list[InboundText] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            pn_id = (value.get("metadata") or {}).get("phone_number_id")
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts") or []
            }
            for m in value.get("messages") or []:
                if m.get("type") != "text":
                    continue
                frm = m.get("from")
                body = (m.get("text") or {}).get("body")
                if not frm or not body:
                    continue
                out.append(
                    InboundText(
                        channel=Channel.WHATSAPP,
                        identity=f"+{frm}",
                        text=body,
                        to=pn_id,
                        name=names.get(frm),
                        provider_ref=m.get("id"),
                    )
                )
    return out


# -- service -------------------------------------------------------------------------------------


class ThreadFilter(BaseModel):
    status: ThreadStatus | None = None
    channel: Channel | None = None
    assigned_to: str | None = None
    unassigned: bool = False
    unread_only: bool = False
    q: str | None = None
    limit: int = 100


class InboxStats(BaseModel):
    open: int = 0
    waiting: int = 0
    unread: int = 0
    unassigned: int = 0
    breached: int = 0
    by_channel: dict[str, int] = Field(default_factory=dict)


class InboxService:
    def __init__(
        self,
        store: CallStore,
        agent: TextAgent,
        senders: dict[Channel, ChannelSender],
        notifications: NotificationService | None = None,
        live: LiveCallHub | None = None,
        on_ticket: Callable[[str, str, TicketIntake], Awaitable[Ticket]] | None = None,
        sla_minutes: int = DEFAULT_SLA_MINUTES,
    ) -> None:
        self.store = store
        self.agent = agent
        self.senders = senders
        self.notifications = notifications
        self.live = live
        self.on_ticket = on_ticket
        self.sla = timedelta(minutes=sla_minutes)

    # -- lookups --------------------------------------------------------------------------------
    async def config_for(self, tenant_id: str, to: str | None = None) -> AssistantConfig | None:
        return await self._cfg(tenant_id, to)

    async def _cfg(self, tenant_id: str, to: str | None = None) -> AssistantConfig | None:
        if to:
            cfg = await self.store.resolve_number(to)
            if cfg is not None and cfg.tenant_id == tenant_id:
                return cfg
        cfgs = await self.store.list_assistants(tenant_id)
        return cfgs[0] if cfgs else None

    async def threads(self, tenant_id: str, f: ThreadFilter | None = None) -> list[Thread]:
        f = f or ThreadFilter()
        docs = await self.store.list_docs(THREAD_KIND, tenant_id, 2000)
        out: list[Thread] = []
        for d in docs:
            t = Thread.from_doc(d)
            if f.status and t.status != f.status:
                continue
            if not f.status and t.status == ThreadStatus.CLOSED:
                continue
            if f.channel and t.channel != f.channel:
                continue
            if f.assigned_to and t.assigned_to != f.assigned_to:
                continue
            if f.unassigned and t.assigned_to:
                continue
            if f.unread_only and not t.unread:
                continue
            if f.q:
                hay = f"{t.identity} {t.contact_name or ''} {t.subject or ''} {t.last_preview}"
                if f.q.lower() not in hay.lower():
                    continue
            out.append(t)
        out.sort(key=lambda t: t.last_message_at, reverse=True)
        return out[: f.limit]

    async def stats(self, tenant_id: str) -> InboxStats:
        s = InboxStats()
        for t in await self.threads(tenant_id, ThreadFilter(limit=5000)):
            if t.status == ThreadStatus.OPEN:
                s.open += 1
            elif t.status == ThreadStatus.WAITING:
                s.waiting += 1
            s.unread += 1 if t.unread else 0
            s.unassigned += 1 if not t.assigned_to else 0
            s.breached += 1 if t.sla_breached else 0
            s.by_channel[t.channel] = s.by_channel.get(t.channel, 0) + 1
        return s

    async def get(self, tenant_id: str, thread_id: str) -> Thread | None:
        d = await self.store.get_doc(THREAD_KIND, thread_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return Thread.from_doc(d)

    async def messages(self, tenant_id: str, thread_id: str) -> list[InboxMessage]:
        docs = await self.store.list_docs(MESSAGE_KIND, tenant_id, 5000)
        msgs = [InboxMessage.from_doc(d) for d in docs if d.data.get("thread_id") == thread_id]
        msgs.sort(key=lambda m: m.created_at)
        return msgs

    async def find_existing(
        self, tenant_id: str, channel: Channel, identity: str, *, include_closed: bool = False
    ) -> Thread | None:
        """Open thread for this identity; with include_closed, the most recent one of any status."""
        latest: Thread | None = None
        for d in await self.store.list_docs(THREAD_KIND, tenant_id, 2000):
            t = Thread.from_doc(d)
            if t.channel != channel or t.identity != identity:
                continue
            if t.status != ThreadStatus.CLOSED:
                return t
            if include_closed and (latest is None or t.created_at > latest.created_at):
                latest = t
        return latest

    async def find_or_open(
        self,
        tenant_id: str,
        company_id: str,
        channel: Channel,
        identity: str,
        *,
        name: str | None = None,
        contact_id: str | None = None,
    ) -> Thread:
        for d in await self.store.list_docs(THREAD_KIND, tenant_id, 2000):
            t = Thread.from_doc(d)
            if t.channel == channel and t.identity == identity and t.status != ThreadStatus.CLOSED:
                if name and not t.contact_name:
                    t.contact_name = name
                return t
        t = Thread(
            tenant_id=tenant_id,
            company_id=company_id,
            channel=channel,
            identity=identity,
            contact_name=name,
            contact_id=contact_id,
        )
        if t.contact_id is None and channel != Channel.WEBCHAT:
            t.contact_id, _ = await self.store.touch_contact(tenant_id, company_id, identity)
        if t.contact_id is not None:
            c = await self.store.get_contact(t.contact_id)
            if c is not None and c.name and not t.contact_name:
                t.contact_name = c.name
        await self.store.put_doc(t.to_doc())
        return t

    # -- write paths ----------------------------------------------------------------------------
    async def _append(self, t: Thread, m: InboxMessage, *, count_unread: bool) -> None:
        await self.store.put_doc(m.to_doc())
        t.message_count += 1
        t.last_message_at = m.created_at
        if m.direction != Direction.NOTE:
            t.last_preview = m.text[:140]
            t.last_direction = m.direction
        if count_unread:
            t.unread += 1
        if m.direction == Direction.OUT:
            t.unread = 0
            t.sla_due_at = None
            t.sla_breached = False
        await self.store.put_doc(t.to_doc())
        self._publish("inbox.message", t, m)

    def _publish(self, kind: str, t: Thread, m: InboxMessage | None = None) -> None:
        if self.live is None:
            return
        self.live.publish(
            LiveMessage(
                type=kind,
                tenant_id=t.tenant_id,
                payload={
                    "thread": t.model_dump(mode="json"),
                    "message": m.model_dump(mode="json") if m else None,
                },
            )
        )

    async def inbound(self, tenant_id: str, inb: InboundText) -> tuple[Thread, InboxMessage | None]:
        """Contact -> business text: store it, then let the AI answer unless a human owns it."""
        cfg = await self._cfg(tenant_id, inb.to)
        company_id = cfg.company_id if cfg else f"{tenant_id}-main"
        t = await self.find_or_open(tenant_id, company_id, inb.channel, inb.identity, name=inb.name)
        if t.status == ThreadStatus.CLOSED:
            t.status = ThreadStatus.OPEN
        if t.sla_due_at is None:
            t.sla_due_at = datetime.now(UTC) + self.sla
        m = InboxMessage(
            tenant_id=tenant_id,
            thread_id=t.id,
            channel=inb.channel,
            direction=Direction.IN,
            author=Author.CONTACT,
            author_name=t.contact_name,
            text=inb.text,
            status="received",
            provider_ref=inb.provider_ref,
        )
        await self._append(t, m, count_unread=True)
        if cfg is None or not t.ai_enabled or inb.channel not in TEXT_CHANNELS:
            return t, None
        history = await self.messages(tenant_id, t.id)
        turn = await self.agent.respond(cfg, t, history)
        reply = await self._deliver(
            t, turn.reply, author=Author.AI, author_name=cfg.name, unread_after=1
        )
        if turn.ticket is not None and self.on_ticket is not None:
            try:
                ticket = await self.on_ticket(tenant_id, company_id, turn.ticket)
                reply.ticket_id = ticket.id
                await self.store.put_doc(reply.to_doc())
            except Exception:
                log.warning("inbox ticket creation failed for %s", t.id, exc_info=True)
        if turn.clarifying and not turn.handoff:
            reply.clarifying = True
            await self.store.put_doc(reply.to_doc())
        if turn.handoff:
            reply.handoff = True
            await self.store.put_doc(reply.to_doc())
            t.ai_enabled = False
            t.status = ThreadStatus.WAITING
            t.handoff_department = turn.department
            t.callback_ticket_id = reply.ticket_id
            t.sla_due_at = datetime.now(UTC) + self.sla
            await self.store.put_doc(t.to_doc())
            await self._status(
                t,
                f"Connecting you to {turn.department or 'a team member'}… this can take a "
                "couple of minutes. Please keep this chat open.",
            )
            self._publish("inbox.thread", t)
            self._publish("inbox.handoff", t, m)
            await self._notify(
                t,
                NotifyEvent.INBOX_HANDOFF,
                f"{t.channel} conversation needs a human",
                f"{t.contact_name or t.identity}: {inb.text[:200]}",
            )
        return t, reply

    async def _deliver(
        self,
        t: Thread,
        text: str,
        *,
        author: Author,
        author_name: str | None,
        unread_after: int = 0,
    ) -> InboxMessage:
        sender = self.senders.get(t.channel)
        d = (
            await sender.send(t, text)
            if sender is not None
            else Delivery(status="skipped", error=f"no sender for {t.channel}")
        )
        m = InboxMessage(
            tenant_id=t.tenant_id,
            thread_id=t.id,
            channel=t.channel,
            direction=Direction.OUT,
            author=author,
            author_name=author_name,
            text=text,
            status=d.status,
            error=d.error,
            provider_ref=d.provider_ref,
        )
        await self._append(t, m, count_unread=False)
        if unread_after:
            # AI replies still leave the inbound unread for the team to glance at.
            t.unread = unread_after
            await self.store.put_doc(t.to_doc())
        return m

    async def reply(self, tenant_id: str, thread_id: str, text: str, by: str) -> InboxMessage:
        """Human reply: pauses the AI and takes the thread if unassigned."""
        t = await self._require(tenant_id, thread_id)
        joining = t.last_direction != Direction.OUT or t.status == ThreadStatus.WAITING
        if joining:
            history = await self.messages(tenant_id, t.id)
            joining = not any(m.author == Author.AGENT and m.author_name == by for m in history)
        t.ai_enabled = False
        t.status = ThreadStatus.OPEN
        t.assigned_to = t.assigned_to or by
        if joining:
            await self._status(t, f"{display_name(by)} has joined the chat.")
        m = await self._deliver(t, text, author=Author.AGENT, author_name=by)
        return m

    async def _status(self, t: Thread, text: str) -> InboxMessage | None:
        """Visitor-facing status line (web chat only; other channels would need a real send)."""
        if t.channel != Channel.WEBCHAT:
            return None
        m = InboxMessage(
            tenant_id=t.tenant_id,
            thread_id=t.id,
            channel=t.channel,
            direction=Direction.OUT,
            author=Author.SYSTEM,
            text=text,
        )
        unread, due, breached = t.unread, t.sla_due_at, t.sla_breached
        await self._append(t, m, count_unread=False)
        t.unread, t.sla_due_at, t.sla_breached = unread, due, breached
        await self.store.put_doc(t.to_doc())
        return m

    async def note(self, tenant_id: str, thread_id: str, text: str, by: str) -> InboxMessage:
        t = await self._require(tenant_id, thread_id)
        m = InboxMessage(
            tenant_id=tenant_id,
            thread_id=t.id,
            channel=t.channel,
            direction=Direction.NOTE,
            author=Author.AGENT,
            author_name=by,
            text=text,
        )
        await self._append(t, m, count_unread=False)
        return m

    async def update(
        self,
        tenant_id: str,
        thread_id: str,
        *,
        status: ThreadStatus | None = None,
        assigned_to: str | None = None,
        clear_assignee: bool = False,
        ai_enabled: bool | None = None,
        read: bool | None = None,
        tags: list[str] | None = None,
        subject: str | None = None,
    ) -> Thread:
        t = await self._require(tenant_id, thread_id)
        if status is not None:
            if status == ThreadStatus.CLOSED and t.status != ThreadStatus.CLOSED:
                await self._status(
                    t, "This chat has been closed. Send a message if you need anything else."
                )
            t.status = status
            if status == ThreadStatus.CLOSED:
                t.unread = 0
                t.sla_due_at = None
        if clear_assignee:
            t.assigned_to = None
        elif assigned_to is not None:
            t.assigned_to = assigned_to
        if ai_enabled is not None:
            t.ai_enabled = ai_enabled
            if ai_enabled and t.status == ThreadStatus.WAITING:
                t.status = ThreadStatus.OPEN
        if read is True:
            t.unread = 0
        if tags is not None:
            t.tags = tags
        if subject is not None:
            t.subject = subject
        await self.store.put_doc(t.to_doc())
        self._publish("inbox.thread", t)
        return t

    async def _require(self, tenant_id: str, thread_id: str) -> Thread:
        t = await self.get(tenant_id, thread_id)
        if t is None:
            raise KeyError(thread_id)
        return t

    async def _notify(self, t: Thread, event: NotifyEvent, title: str, body: str) -> None:
        if self.notifications is None:
            return
        try:
            await self.notifications.dispatch(
                NotificationEvent(
                    tenant_id=t.tenant_id,
                    company_id=t.company_id,
                    event=event,
                    title=title,
                    body=body,
                    context={"thread_id": t.id, "channel": t.channel},
                )
            )
        except Exception:
            log.warning("inbox notification failed for %s", t.id, exc_info=True)

    # -- calls into the same timeline -----------------------------------------------------------
    async def on_call_ended(self, call: CallRecord) -> Thread | None:
        if not call.caller or call.caller.startswith("anonymous") or call.caller == "unknown":
            return None
        missed = call.status == "failed" or call.answered_at is None
        mins = int((call.duration_s or 0) // 60)
        secs = int((call.duration_s or 0) % 60)
        if call.caller.startswith(WEB_CALLER_PREFIX):
            # Browser voice: file under the visitor's web-chat thread so chat + voice read as one.
            channel = Channel.WEBCHAT
            identity = call.caller[len(WEB_CALLER_PREFIX) :]
            text = (
                f"Browser voice call ({call.end_reason or 'not connected'})"
                if missed
                else call.summary or f"Browser voice call, {mins}m {secs:02d}s"
            )
        else:
            channel = Channel.VOICEMAIL if missed else Channel.CALL
            identity = call.caller
            text = (
                f"Missed call ({call.end_reason or 'no answer'})"
                if missed
                else call.summary or f"{call.direction.title()} call, {mins}m {secs:02d}s"
            )
        t = await self.find_or_open(
            call.tenant_id, call.company_id, channel, identity, contact_id=call.contact_id
        )
        m = InboxMessage(
            tenant_id=call.tenant_id,
            thread_id=t.id,
            channel=channel,
            direction=Direction.IN if call.direction == "inbound" else Direction.OUT,
            author=Author.SYSTEM,
            text=text,
            call_id=call.call_id,
            status="received",
        )
        await self._append(t, m, count_unread=missed)
        return t

    # -- SLA --------------------------------------------------------------------------------------
    async def sweep_sla(self, now: datetime | None = None) -> list[Thread]:
        now = now or datetime.now(UTC)
        out: list[Thread] = []
        for d in await self.store.list_docs(THREAD_KIND, None, 5000):
            t = Thread.from_doc(d)
            if t.sla_breached or t.sla_due_at is None or t.status == ThreadStatus.CLOSED:
                continue
            if t.sla_due_at <= now:
                t.sla_breached = True
                if (
                    t.status == ThreadStatus.WAITING
                    and not t.ai_enabled
                    and t.callback_ticket_id is None
                    and self.on_ticket is not None
                ):
                    try:
                        ticket = await self.on_ticket(t.tenant_id, t.company_id, _handoff_ticket(t))
                        t.callback_ticket_id = ticket.id
                        await self._status(
                            t,
                            "Sorry, nobody was free to pick up. We've logged your request "
                            f"(ref {ticket.id}) and the team will get back to you.",
                        )
                    except Exception:
                        log.warning("handoff callback ticket failed for %s", t.id, exc_info=True)
                await self.store.put_doc(t.to_doc())
                self._publish("inbox.thread", t)
                await self._notify(
                    t,
                    NotifyEvent.INBOX_SLA_BREACHED,
                    f"Unanswered {t.channel} message",
                    f"{t.contact_name or t.identity} has waited over "
                    f"{int(self.sla.total_seconds() // 60)} min: {t.last_preview}",
                )
                out.append(t)
        return out

    # -- canned replies ---------------------------------------------------------------------------
    async def canned(self, tenant_id: str) -> list[CannedReply]:
        docs = await self.store.list_docs(CANNED_KIND, tenant_id, 500)
        out = [CannedReply.model_validate(d.data) for d in docs]
        out.sort(key=lambda c: c.title.lower())
        return out

    async def save_canned(self, c: CannedReply) -> CannedReply:
        await self.store.put_doc(c.to_doc())
        return c

    async def delete_canned(self, tenant_id: str, canned_id: str) -> bool:
        d = await self.store.get_doc(CANNED_KIND, canned_id)
        if d is None or d.tenant_id != tenant_id:
            return False
        return await self.store.delete_doc(CANNED_KIND, canned_id)

    # -- web chat widget -------------------------------------------------------------------------
    async def widget(self, tenant_id: str) -> ChatWidget | None:
        docs = await self.store.list_docs(WIDGET_KIND, tenant_id, 1)
        return ChatWidget.model_validate(docs[0].data) if docs else None

    async def ensure_widget(self, tenant_id: str) -> ChatWidget:
        w = await self.widget(tenant_id)
        if w is None:
            cfg = await self._cfg(tenant_id)
            w = ChatWidget(
                tenant_id=tenant_id,
                company_id=cfg.company_id if cfg else f"{tenant_id}-main",
                title=f"Chat with {cfg.business_name}" if cfg else "Chat with us",
            )
            await self.store.put_doc(w.to_doc())
        return w

    async def save_widget(self, w: ChatWidget) -> ChatWidget:
        await self.store.put_doc(w.to_doc())
        return w

    async def widget_by_token(self, token: str) -> ChatWidget | None:
        for d in await self.store.list_docs(WIDGET_KIND, None, 5000):
            w = ChatWidget.model_validate(d.data)
            if hmac.compare_digest(w.token, token):
                return w
        return None

    # -- whatsapp account ------------------------------------------------------------------------
    async def whatsapp(self, tenant_id: str) -> WhatsAppAccount | None:
        docs = await self.store.list_docs(WHATSAPP_KIND, tenant_id, 1)
        return WhatsAppAccount.model_validate(docs[0].data) if docs else None

    async def all_whatsapp(self) -> list[WhatsAppAccount]:
        docs = await self.store.list_docs(WHATSAPP_KIND, None, 5000)
        return [WhatsAppAccount.model_validate(d.data) for d in docs]

    async def whatsapp_by_phone_id(self, phone_number_id: str) -> WhatsAppAccount | None:
        for d in await self.store.list_docs(WHATSAPP_KIND, None, 5000):
            if d.data.get("phone_number_id") == phone_number_id:
                return WhatsAppAccount.model_validate(d.data)
        return None

    async def save_whatsapp(self, acct: WhatsAppAccount) -> WhatsAppAccount:
        existing = await self.whatsapp(acct.tenant_id)
        if existing is not None and existing.id != acct.id:
            await self.store.delete_doc(WHATSAPP_KIND, existing.id)
        await self.store.put_doc(acct.to_doc())
        return acct


class InboxSlaLoop:
    """Background loop flagging threads whose reply SLA has lapsed."""

    def __init__(self, service: InboxService, interval_s: float = 60.0) -> None:
        self._svc = service
        self._interval = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="inbox-sla")

    async def _run(self) -> None:
        while True:
            try:
                await self._svc.sweep_sla()
            except Exception:
                log.exception("inbox sla sweep failed")
            await asyncio.sleep(self._interval)

    async def aclose(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
