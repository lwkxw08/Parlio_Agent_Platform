"""Phase 11: omnichannel shared inbox.

* ``/v1/inbox/*``                 dashboard: threads, messages, reply/note, assign, canned replies,
                                  web-chat widget + WhatsApp account settings.
* ``/v1/inbound/sms``             carrier (Telnyx) inbound SMS webhook.
* ``/v1/inbound/whatsapp``        Meta WhatsApp Cloud API verification + message webhook.
* ``/v1/public/chat/{token}/*``   web chat widget (embed token, per-visitor polling).
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.browser_voice import WebVoiceSession
from parlio_api.deps import AuditDep, BrowserVoiceDep, InboxDep, SettingsDep, StoreDep
from parlio_api.inbox import (
    CannedReply,
    Channel,
    ChatWidget,
    InboundText,
    InboxMessage,
    InboxStats,
    Thread,
    ThreadFilter,
    ThreadStatus,
    WhatsAppAccount,
    parse_meta_whatsapp,
    parse_telnyx_sms,
    verify_meta_signature,
)
from parlio_api.observability import AuditEntry

log = logging.getLogger("parlio.api.inbox")

router = APIRouter(prefix="/v1/inbox", tags=["inbox"])
inbound = APIRouter(prefix="/v1/inbound", tags=["inbound"])
public = APIRouter(prefix="/v1/public/chat", tags=["public"])


def _audit(request: Request, user: UserDep, tenant_id: str, action: str, target: str) -> AuditEntry:
    return AuditEntry(
        tenant_id=tenant_id,
        actor=user.email,
        action=action,
        target=target,
        method=request.method,
        path=request.url.path,
        ip=request.client.host if request.client else None,
    )


# -- dashboard --------------------------------------------------------------------------------


@router.get("/threads", response_model=list[Thread])
async def list_threads(
    user: UserDep,
    inbox: InboxDep,
    tenant_id: str,
    status_: Annotated[ThreadStatus | None, Query(alias="status")] = None,
    channel: Channel | None = None,
    assigned_to: str | None = None,
    unassigned: bool = False,
    unread_only: bool = False,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[Thread]:
    user.require_tenant(tenant_id)
    f = ThreadFilter(
        status=status_,
        channel=channel,
        assigned_to=assigned_to,
        unassigned=unassigned,
        unread_only=unread_only,
        q=q,
        limit=limit,
    )
    return await inbox.threads(tenant_id, f)


@router.get("/stats", response_model=InboxStats)
async def stats(user: UserDep, inbox: InboxDep, tenant_id: str) -> InboxStats:
    user.require_tenant(tenant_id)
    return await inbox.stats(tenant_id)


class ThreadDetail(BaseModel):
    thread: Thread
    messages: list[InboxMessage]


@router.get("/threads/{thread_id}", response_model=ThreadDetail)
async def get_thread(
    user: UserDep, inbox: InboxDep, thread_id: str, tenant_id: str
) -> ThreadDetail:
    user.require_tenant(tenant_id)
    t = await inbox.get(tenant_id, thread_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found")
    return ThreadDetail(thread=t, messages=await inbox.messages(tenant_id, thread_id))


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/threads/{thread_id}/reply", response_model=InboxMessage)
async def reply(
    request: Request,
    user: UserDep,
    inbox: InboxDep,
    audit: AuditDep,
    thread_id: str,
    tenant_id: str,
    body: TextIn,
) -> InboxMessage:
    user.require_tenant(tenant_id)
    try:
        m = await inbox.reply(tenant_id, thread_id, body.text, by=user.email)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found") from None
    await audit.record(_audit(request, user, tenant_id, "inbox.reply", thread_id))
    return m


@router.post("/threads/{thread_id}/note", response_model=InboxMessage)
async def note(
    user: UserDep, inbox: InboxDep, thread_id: str, tenant_id: str, body: TextIn
) -> InboxMessage:
    user.require_tenant(tenant_id)
    try:
        return await inbox.note(tenant_id, thread_id, body.text, by=user.email)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found") from None


class ThreadPatch(BaseModel):
    status: ThreadStatus | None = None
    assigned_to: str | None = None
    clear_assignee: bool = False
    ai_enabled: bool | None = None
    read: bool | None = None
    tags: list[str] | None = None
    subject: str | None = Field(default=None, max_length=120)


@router.patch("/threads/{thread_id}", response_model=Thread)
async def patch_thread(
    request: Request,
    user: UserDep,
    inbox: InboxDep,
    audit: AuditDep,
    thread_id: str,
    tenant_id: str,
    body: ThreadPatch,
) -> Thread:
    user.require_tenant(tenant_id)
    try:
        t = await inbox.update(
            tenant_id,
            thread_id,
            status=body.status,
            assigned_to=body.assigned_to,
            clear_assignee=body.clear_assignee,
            ai_enabled=body.ai_enabled,
            read=body.read,
            tags=body.tags,
            subject=body.subject,
        )
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found") from None
    if body.status is not None or body.assigned_to is not None or body.ai_enabled is not None:
        await audit.record(_audit(request, user, tenant_id, "inbox.update", thread_id))
    return t


# -- canned replies ---------------------------------------------------------------------------


class CannedIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    shortcut: str | None = Field(default=None, max_length=24)
    body: str = Field(min_length=1, max_length=1600)


@router.get("/canned", response_model=list[CannedReply])
async def list_canned(user: UserDep, inbox: InboxDep, tenant_id: str) -> list[CannedReply]:
    user.require_tenant(tenant_id)
    return await inbox.canned(tenant_id)


@router.post("/canned", response_model=CannedReply, status_code=status.HTTP_201_CREATED)
async def create_canned(
    user: UserDep, inbox: InboxDep, tenant_id: str, body: CannedIn
) -> CannedReply:
    user.require_tenant(tenant_id)
    return await inbox.save_canned(CannedReply(tenant_id=tenant_id, **body.model_dump()))


@router.put("/canned/{canned_id}", response_model=CannedReply)
async def update_canned(
    user: UserDep, inbox: InboxDep, canned_id: str, tenant_id: str, body: CannedIn
) -> CannedReply:
    user.require_tenant(tenant_id)
    existing = next((c for c in await inbox.canned(tenant_id) if c.id == canned_id), None)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "canned reply not found")
    return await inbox.save_canned(existing.model_copy(update=body.model_dump()))


@router.delete("/canned/{canned_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canned(user: UserDep, inbox: InboxDep, canned_id: str, tenant_id: str) -> None:
    user.require_tenant(tenant_id)
    if not await inbox.delete_canned(tenant_id, canned_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "canned reply not found")


# -- channel settings ---------------------------------------------------------------------------


class WidgetOut(BaseModel):
    id: str
    token: str
    enabled: bool
    title: str
    greeting: str
    colour: str
    allowed_origins: list[str]
    voice_enabled: bool
    embed_url: str
    snippet: str


def _widget_out(w: ChatWidget, dashboard_url: str) -> WidgetOut:
    embed = f"{dashboard_url.rstrip('/')}/chat/{w.token}"
    snippet = (
        f'<iframe src="{embed}" title="{w.title}" '
        'style="position:fixed;bottom:16px;right:16px;width:380px;height:560px;'
        'border:0;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.25)"></iframe>'
    )
    return WidgetOut(
        id=w.id,
        token=w.token,
        enabled=w.enabled,
        title=w.title,
        greeting=w.greeting,
        colour=w.colour,
        allowed_origins=w.allowed_origins,
        voice_enabled=w.voice_enabled,
        embed_url=embed,
        snippet=snippet,
    )


class WidgetPatch(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=60)
    greeting: str | None = Field(default=None, min_length=1, max_length=300)
    colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    allowed_origins: list[str] | None = None
    voice_enabled: bool | None = None
    rotate_token: bool = False


@router.get("/widget", response_model=WidgetOut)
async def get_widget(
    user: UserDep, inbox: InboxDep, settings: SettingsDep, tenant_id: str
) -> WidgetOut:
    user.require_tenant(tenant_id)
    return _widget_out(await inbox.ensure_widget(tenant_id), settings.dashboard_url)


@router.patch("/widget", response_model=WidgetOut)
async def patch_widget(
    user: UserDep, inbox: InboxDep, settings: SettingsDep, tenant_id: str, body: WidgetPatch
) -> WidgetOut:
    user.require_admin(tenant_id)
    w = await inbox.ensure_widget(tenant_id)
    update = body.model_dump(exclude_none=True, exclude={"rotate_token"})
    w = w.model_copy(update=update)
    if body.rotate_token:
        w = w.model_copy(update={"token": secrets.token_urlsafe(18)})
    return _widget_out(await inbox.save_widget(w), settings.dashboard_url)


class WhatsAppIn(BaseModel):
    phone_number_id: str = Field(min_length=3, max_length=40)
    display_number: str | None = Field(default=None, max_length=20)
    access_token: str | None = Field(default=None, max_length=512)


class WhatsAppOut(BaseModel):
    id: str
    phone_number_id: str
    display_number: str | None
    has_token: bool
    verify_token: str
    webhook_url: str


def _wa_out(a: WhatsAppAccount, api_url: str) -> WhatsAppOut:
    return WhatsAppOut(
        id=a.id,
        phone_number_id=a.phone_number_id,
        display_number=a.display_number,
        has_token=bool(a.access_token),
        verify_token=a.verify_token,
        webhook_url=f"{api_url.rstrip('/')}/v1/inbound/whatsapp",
    )


@router.get("/whatsapp", response_model=WhatsAppOut | None)
async def get_whatsapp(
    user: UserDep, inbox: InboxDep, settings: SettingsDep, tenant_id: str
) -> WhatsAppOut | None:
    user.require_tenant(tenant_id)
    a = await inbox.whatsapp(tenant_id)
    return _wa_out(a, settings.public_api_url) if a else None


@router.put("/whatsapp", response_model=WhatsAppOut)
async def put_whatsapp(
    request: Request,
    user: UserDep,
    inbox: InboxDep,
    audit: AuditDep,
    settings: SettingsDep,
    tenant_id: str,
    body: WhatsAppIn,
) -> WhatsAppOut:
    user.require_admin(tenant_id)
    existing = await inbox.whatsapp(tenant_id)
    cfg = await inbox.config_for(tenant_id)
    acct = WhatsAppAccount(
        tenant_id=tenant_id,
        company_id=cfg.company_id if cfg else f"{tenant_id}-main",
        phone_number_id=body.phone_number_id,
        display_number=body.display_number,
        access_token=body.access_token,
    )
    if existing is not None:
        acct = acct.model_copy(
            update={
                "id": existing.id,
                "verify_token": existing.verify_token,
                "access_token": body.access_token or existing.access_token,
            }
        )
    await inbox.save_whatsapp(acct)
    await audit.record(_audit(request, user, tenant_id, "inbox.whatsapp.configure", acct.id))
    return _wa_out(acct, settings.public_api_url)


# -- carrier / Meta webhooks ---------------------------------------------------------------------


@inbound.post("/sms", status_code=status.HTTP_202_ACCEPTED)
async def inbound_sms(
    request: Request,
    inbox: InboxDep,
    store: StoreDep,
    settings: SettingsDep,
    secret: str | None = None,
) -> dict[str, Any]:
    if settings.inbound_webhook_secret and secret != settings.inbound_webhook_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad webhook secret")
    payload = await request.json()
    inb = parse_telnyx_sms(payload)
    if inb is None:
        return {"ignored": True}
    cfg = await store.resolve_number(inb.to) if inb.to else None
    if cfg is None:
        log.info("inbound sms to unknown number %s", inb.to)
        return {"ignored": True, "reason": "unknown number"}
    t, reply = await inbox.inbound(cfg.tenant_id, inb)
    return {"thread_id": t.id, "replied": reply is not None}


@inbound.get("/whatsapp")
async def whatsapp_verify(
    inbox: InboxDep,
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    if hub_mode != "subscribe" or not hub_verify_token or hub_challenge is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad verification request")
    for a in await inbox.all_whatsapp():
        if a.verify_token == hub_verify_token:
            return Response(hub_challenge, media_type="text/plain")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "unknown verify token")


@inbound.post("/whatsapp", status_code=status.HTTP_200_OK)
async def whatsapp_webhook(
    request: Request, inbox: InboxDep, settings: SettingsDep
) -> dict[str, Any]:
    body = await request.body()
    if not verify_meta_signature(
        settings.whatsapp_app_secret, body, request.headers.get("x-hub-signature-256")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")
    payload = await request.json()
    handled = 0
    for inb in parse_meta_whatsapp(payload):
        acct = await inbox.whatsapp_by_phone_id(inb.to or "")
        if acct is None:
            log.info("whatsapp message for unknown phone_number_id %s", inb.to)
            continue
        await inbox.inbound(acct.tenant_id, inb)
        handled += 1
    return {"handled": handled}


# -- public web chat ---------------------------------------------------------------------------


class ChatConfig(BaseModel):
    title: str
    greeting: str
    colour: str
    enabled: bool
    voice_enabled: bool


async def _widget(inbox: InboxDep, token: str) -> ChatWidget:
    w = await inbox.widget_by_token(token)
    if w is None or not w.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chat unavailable")
    return w


@public.get("/{token}", response_model=ChatConfig)
async def chat_config(inbox: InboxDep, token: str) -> ChatConfig:
    return ChatConfig(**(await _widget(inbox, token)).public())


class ChatSend(BaseModel):
    visitor: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=80)


class ChatMessageOut(BaseModel):
    id: str
    direction: str
    author: str
    author_name: str | None
    text: str
    created_at: str


def _chat_msg(m: InboxMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=m.id,
        direction=m.direction.value,
        author=m.author.value,
        author_name=m.author_name,
        text=m.text,
        created_at=m.created_at.isoformat(),
    )


@public.post("/{token}/messages", response_model=list[ChatMessageOut])
async def chat_send(inbox: InboxDep, token: str, body: ChatSend) -> list[ChatMessageOut]:
    w = await _widget(inbox, token)
    inb = InboundText(
        channel=Channel.WEBCHAT, identity=body.visitor, text=body.text, name=body.name
    )
    t, _ = await inbox.inbound(w.tenant_id, inb)
    return [_chat_msg(m) for m in await inbox.messages(w.tenant_id, t.id) if m.direction != "note"]


class VoiceStart(BaseModel):
    visitor: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str | None = Field(default=None, max_length=80)
    page_url: str | None = Field(default=None, max_length=500)


@public.post("/{token}/voice", response_model=WebVoiceSession)
async def chat_voice_start(
    inbox: InboxDep, voice: BrowserVoiceDep, token: str, body: VoiceStart
) -> WebVoiceSession:
    """Browser "click to talk": dispatch the voice worker and return a LiveKit join token."""
    w = await _widget(inbox, token)
    if not w.voice_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice unavailable")
    session = await voice.start(w.tenant_id, body.visitor, body.name, body.page_url)
    if session is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no assistant configured")
    return session


@public.get("/{token}/messages", response_model=list[ChatMessageOut])
async def chat_poll(
    inbox: InboxDep,
    token: str,
    visitor: str = Query(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
) -> list[ChatMessageOut]:
    w = await _widget(inbox, token)
    t = await inbox.find_existing(w.tenant_id, Channel.WEBCHAT, visitor)
    if t is None:
        return []
    return [_chat_msg(m) for m in await inbox.messages(w.tenant_id, t.id) if m.direction != "note"]
