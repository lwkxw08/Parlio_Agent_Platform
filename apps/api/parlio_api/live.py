"""Phase 10: live call monitoring, supervisor takeover and human-in-the-loop approvals.

* ``LiveCallHub`` keeps the set of in-progress calls (folded from the same worker events the
  store consumes) and fans every event out to per-tenant subscribers (dashboard WebSockets).
* ``RoomControl`` sends supervisor commands (whisper / say / take over / hand back / hang up) to
  the voice worker over the LiveKit room data channel and mints browser join tokens so a human
  can listen in (subscribe-only) or speak to the caller (publish) from the dashboard.
* ``ApprovalService`` lets the AI pause mid-call and ask the owner to approve a quote, booking,
  refund or similar; the owner taps approve/reject in the dashboard or via a one-time link sent
  through the tenant's notification rules (SMS/Slack/email).
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections import deque
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import uuid4

from livekit import api
from pydantic import BaseModel, Field

from parlio_api.notifications import NotificationEvent, NotificationService, NotifyEvent
from parlio_api.store import CallStore, TenantDoc
from parlio_voice.models import CallEvent, CallEventType

log = logging.getLogger("parlio.api.live")

APPROVAL_KIND = "approval"
CONTROL_TOPIC = "parlio.control"
TRANSCRIPT_TAIL = 200

SupervisorMode = Literal["none", "listening", "taken_over"]


# -- live calls -------------------------------------------------------------------------------


class LiveCall(BaseModel):
    call_id: str
    tenant_id: str
    assistant_id: str
    room: str | None = None
    caller: str | None = None
    dialed: str | None = None
    direction: str = "inbound"
    status: str = "ringing"  # ringing | in_progress | transferring
    started_at: datetime
    answered_at: datetime | None = None
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    escalated: bool = False
    transfers: int = 0
    tickets: int = 0
    supervisor: str | None = None
    supervisor_mode: SupervisorMode = "none"
    pending_approval_id: str | None = None

    def apply(self, ev: CallEvent) -> None:
        p = ev.payload
        match ev.type:
            case CallEventType.CALL_STARTED:
                self.caller = p.get("caller")
                self.dialed = p.get("dialed")
                self.room = p.get("room")
                self.direction = str(p.get("direction", "inbound"))
            case CallEventType.CALL_ANSWERED:
                self.status = "in_progress"
                self.answered_at = ev.occurred_at
            case CallEventType.TRANSCRIPT_ITEM:
                self.transcript.append(
                    {
                        "role": p.get("role"),
                        "text": p.get("text"),
                        "at": ev.occurred_at.isoformat(),
                    }
                )
                del self.transcript[:-TRANSCRIPT_TAIL]
            case CallEventType.SUPERVISOR:
                self.transcript.append(
                    {
                        "role": "supervisor",
                        "text": p.get("text") or f"[{p.get('cmd')} by {p.get('by')}]",
                        "at": ev.occurred_at.isoformat(),
                    }
                )
            case CallEventType.TRANSFER_STARTED:
                self.status = "transferring"
            case CallEventType.TRANSFER_COMPLETED:
                self.transfers += 1
                self.status = "in_progress"
            case CallEventType.TICKET_CREATED:
                self.tickets += 1
            case CallEventType.ESCALATION:
                self.escalated = True
            case _:
                pass


class LiveMessage(BaseModel):
    """One WebSocket frame to the dashboard."""

    type: str  # snapshot | call.* event types | approval.requested | approval.decided
    tenant_id: str
    call_id: str | None = None
    call: LiveCall | None = None
    calls: list[LiveCall] | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LiveCallHub:
    """In-memory registry of active calls + per-tenant fan-out queues."""

    def __init__(self, max_queue: int = 500) -> None:
        self._calls: dict[str, LiveCall] = {}
        self._subs: dict[str, set[asyncio.Queue[LiveMessage]]] = {}
        self._recent_ended: deque[str] = deque(maxlen=1000)
        self._max_queue = max_queue

    # -- queries ---
    def active(self, tenant_id: str) -> list[LiveCall]:
        return sorted(
            (c for c in self._calls.values() if c.tenant_id == tenant_id),
            key=lambda c: c.started_at,
        )

    def get(self, call_id: str) -> LiveCall | None:
        return self._calls.get(call_id)

    def count(self, tenant_id: str | None = None) -> int:
        if tenant_id is None:
            return len(self._calls)
        return sum(1 for c in self._calls.values() if c.tenant_id == tenant_id)

    # -- events ---
    def on_event(self, ev: CallEvent) -> None:
        if ev.type in (CallEventType.CALL_ENDED, CallEventType.CALL_FAILED):
            call = self._calls.pop(ev.call_id, None)
            self._recent_ended.append(ev.call_id)
            if call is None:
                return
            self.publish(
                LiveMessage(
                    type=ev.type.value,
                    tenant_id=ev.tenant_id,
                    call_id=ev.call_id,
                    call=call,
                    payload={"reason": ev.payload.get("reason")},
                )
            )
            return
        if ev.call_id in self._recent_ended:
            return  # late event for a finished call
        call = self._calls.get(ev.call_id)
        if call is None:
            call = LiveCall(
                call_id=ev.call_id,
                tenant_id=ev.tenant_id,
                assistant_id=ev.assistant_id,
                started_at=ev.occurred_at,
            )
            self._calls[ev.call_id] = call
        call.apply(ev)
        payload = (
            ev.payload
            if ev.type in (CallEventType.TRANSCRIPT_ITEM, CallEventType.SUPERVISOR)
            else {}
        )
        self.publish(
            LiveMessage(
                type=ev.type.value,
                tenant_id=ev.tenant_id,
                call_id=ev.call_id,
                call=call,
                payload=payload,
            )
        )

    def set_supervisor(self, call_id: str, who: str | None, mode: SupervisorMode) -> LiveCall:
        call = self._calls[call_id]
        call.supervisor = who
        call.supervisor_mode = mode
        self.publish(
            LiveMessage(type="supervisor", tenant_id=call.tenant_id, call_id=call_id, call=call)
        )
        return call

    # -- subscriptions ---
    def subscribe(self, tenant_id: str) -> asyncio.Queue[LiveMessage]:
        q: asyncio.Queue[LiveMessage] = asyncio.Queue(maxsize=self._max_queue)
        self._subs.setdefault(tenant_id, set()).add(q)
        q.put_nowait(
            LiveMessage(type="snapshot", tenant_id=tenant_id, calls=self.active(tenant_id))
        )
        return q

    def unsubscribe(self, tenant_id: str, q: asyncio.Queue[LiveMessage]) -> None:
        subs = self._subs.get(tenant_id)
        if subs:
            subs.discard(q)
            if not subs:
                del self._subs[tenant_id]

    def publish(self, msg: LiveMessage) -> None:
        for q in list(self._subs.get(msg.tenant_id, ())):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # slow consumer: drop the oldest frame rather than block the event pipeline
                with_room = q.get_nowait()
                log.debug("live queue full; dropped %s", with_room.type)
                q.put_nowait(msg)


# -- supervisor control -----------------------------------------------------------------------


class Command(StrEnum):
    WHISPER = "whisper"  # coach the AI (text the caller never hears)
    SAY = "say"  # make the AI speak a given sentence
    TAKEOVER = "takeover"  # AI goes quiet; supervisor speaks to the caller
    HANDBACK = "handback"  # AI resumes
    HANGUP = "hangup"


class ControlMessage(BaseModel):
    cmd: Command
    text: str | None = None
    by: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RoomControl(Protocol):
    async def send(self, room: str, msg: ControlMessage) -> None: ...

    def join_token(self, room: str, identity: str, name: str, *, can_publish: bool) -> str: ...

    @property
    def url(self) -> str | None: ...


class SimulatedRoomControl:
    """Records commands; used in tests and when LiveKit isn't configured."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, ControlMessage]] = []

    async def send(self, room: str, msg: ControlMessage) -> None:
        self.sent.append((room, msg))

    def join_token(self, room: str, identity: str, name: str, *, can_publish: bool) -> str:
        return f"sim-{room}-{identity}-{'pub' if can_publish else 'sub'}"

    @property
    def url(self) -> str | None:
        return None


class LiveKitRoomControl:
    def __init__(self, url: str, key: str, secret: str, ws_url: str | None = None) -> None:
        self._url, self._key, self._secret = url, key, secret
        self._ws_url = ws_url or url.replace("https://", "wss://").replace("http://", "ws://")
        self._lk = api.LiveKitAPI(url, key, secret)

    async def send(self, room: str, msg: ControlMessage) -> None:
        await self._lk.room.send_data(
            api.SendDataRequest(
                room=room,
                data=msg.model_dump_json().encode(),
                kind=api.DataPacket.RELIABLE,
                topic=CONTROL_TOPIC,
            )
        )

    def join_token(self, room: str, identity: str, name: str, *, can_publish: bool) -> str:
        grants = api.VideoGrants(
            room_join=True,
            room=room,
            can_publish=can_publish,
            can_subscribe=True,
            can_publish_data=False,
            hidden=not can_publish,
        )
        return (
            api.AccessToken(self._key, self._secret)
            .with_identity(identity)
            .with_name(name)
            .with_grants(grants)
            .with_ttl(timedelta(hours=2))
            .to_jwt()
        )

    @property
    def url(self) -> str | None:
        return self._ws_url

    async def aclose(self) -> None:
        await self._lk.aclose()


class JoinInfo(BaseModel):
    url: str | None
    token: str
    room: str
    identity: str
    mode: SupervisorMode


class SupervisorService:
    def __init__(self, hub: LiveCallHub, control: RoomControl) -> None:
        self.hub = hub
        self.control = control

    def _call(self, call_id: str, tenant_id: str) -> LiveCall:
        call = self.hub.get(call_id)
        if call is None or call.tenant_id != tenant_id:
            raise LookupError("call is not active")
        if not call.room:
            raise LookupError("call has no room yet")
        return call

    def join(self, call_id: str, tenant_id: str, user_id: str, name: str) -> JoinInfo:
        call = self._call(call_id, tenant_id)
        identity = f"supervisor-{user_id}"
        mode: SupervisorMode = call.supervisor_mode if call.supervisor == user_id else "listening"
        if call.supervisor_mode == "none" or call.supervisor is None:
            mode = "listening"
            self.hub.set_supervisor(call_id, user_id, mode)
        assert call.room
        return JoinInfo(
            url=self.control.url,
            token=self.control.join_token(
                call.room, identity, name, can_publish=mode == "taken_over"
            ),
            room=call.room,
            identity=identity,
            mode=mode,
        )

    async def command(
        self, call_id: str, tenant_id: str, user_id: str, name: str, cmd: Command, text: str | None
    ) -> JoinInfo | None:
        call = self._call(call_id, tenant_id)
        assert call.room
        if cmd in (Command.WHISPER, Command.SAY) and not (text and text.strip()):
            raise ValueError("text is required")
        if (
            call.supervisor
            and call.supervisor != user_id
            and call.supervisor_mode == "taken_over"
            and cmd != Command.HANGUP
        ):
            raise PermissionError(f"call is being handled by {call.supervisor}")
        await self.control.send(call.room, ControlMessage(cmd=cmd, text=text, by=name))
        if cmd == Command.TAKEOVER:
            self.hub.set_supervisor(call_id, user_id, "taken_over")
            return JoinInfo(
                url=self.control.url,
                token=self.control.join_token(
                    call.room, f"supervisor-{user_id}", name, can_publish=True
                ),
                room=call.room,
                identity=f"supervisor-{user_id}",
                mode="taken_over",
            )
        if cmd == Command.HANDBACK:
            self.hub.set_supervisor(call_id, user_id, "listening")
        if cmd == Command.HANGUP:
            self.hub.set_supervisor(call_id, user_id, "none")
        return None

    def leave(self, call_id: str, tenant_id: str, user_id: str) -> None:
        call = self.hub.get(call_id)
        if call is not None and call.tenant_id == tenant_id and call.supervisor == user_id:
            self.hub.set_supervisor(call_id, None, "none")


# -- approvals --------------------------------------------------------------------------------


class ApprovalKind(StrEnum):
    QUOTE = "quote"
    BOOKING = "booking"
    REFUND = "refund"
    DISCOUNT = "discount"
    OTHER = "other"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: f"ap-{uuid4().hex[:10]}")
    tenant_id: str
    company_id: str | None = None
    call_id: str | None = None
    kind: ApprovalKind = ApprovalKind.OTHER
    title: str
    details: str = ""
    amount: float | None = None
    currency: str = "GBP"
    caller: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    token: str = Field(default_factory=lambda: secrets.token_urlsafe(24))
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    note: str | None = None

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=APPROVAL_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.requested_at,
        )

    def public(self) -> dict[str, Any]:
        """What the approve-by-link page may see (no token)."""
        return self.model_dump(mode="json", exclude={"token"})


class ApprovalRequest(BaseModel):
    call_id: str | None = None
    kind: ApprovalKind = ApprovalKind.OTHER
    title: str = Field(min_length=3, max_length=200)
    details: str = Field(default="", max_length=2000)
    amount: float | None = Field(default=None, ge=0)
    currency: str = "GBP"
    caller: str | None = None
    timeout_s: int = Field(default=120, ge=15, le=900)


class ApprovalService:
    def __init__(
        self,
        store: CallStore,
        hub: LiveCallHub,
        notifications: NotificationService,
        dashboard_url: str,
    ) -> None:
        self.store = store
        self.hub = hub
        self.notifications = notifications
        self.dashboard_url = dashboard_url.rstrip("/")

    async def _load(self, approval_id: str) -> Approval | None:
        doc = await self.store.get_doc(APPROVAL_KIND, approval_id)
        if doc is None:
            return None
        ap = Approval.model_validate(doc.data)
        if ap.status == ApprovalStatus.PENDING and ap.expires_at <= datetime.now(UTC):
            ap.status = ApprovalStatus.EXPIRED
            await self.store.put_doc(ap.to_doc())
        return ap

    async def get(self, tenant_id: str, approval_id: str) -> Approval | None:
        ap = await self._load(approval_id)
        return ap if ap is not None and ap.tenant_id == tenant_id else None

    async def by_token(self, token: str) -> Approval | None:
        # token lookup is rare (tap-to-approve from SMS/Slack); scan the tenant-agnostic kind
        for doc in await self.store.list_docs(APPROVAL_KIND, None, limit=1000):
            if secrets.compare_digest(str(doc.data.get("token", "")), token):
                return await self._load(doc.id)
        return None

    async def list(
        self, tenant_id: str, status: ApprovalStatus | None = None, limit: int = 100
    ) -> list[Approval]:
        out: list[Approval] = []
        for doc in await self.store.list_docs(APPROVAL_KIND, tenant_id, limit=limit):
            ap = await self._load(doc.id)
            if ap is not None and (status is None or ap.status == status):
                out.append(ap)
        out.sort(key=lambda a: a.requested_at, reverse=True)
        return out

    async def request(
        self, tenant_id: str, company_id: str | None, req: ApprovalRequest
    ) -> Approval:
        ap = Approval(
            tenant_id=tenant_id,
            company_id=company_id,
            call_id=req.call_id,
            kind=req.kind,
            title=req.title,
            details=req.details,
            amount=req.amount,
            currency=req.currency,
            caller=req.caller,
            expires_at=datetime.now(UTC) + timedelta(seconds=req.timeout_s),
        )
        await self.store.put_doc(ap.to_doc())
        if req.call_id:
            call = self.hub.get(req.call_id)
            if call is not None:
                call.pending_approval_id = ap.id
        self.hub.publish(
            LiveMessage(
                type="approval.requested",
                tenant_id=tenant_id,
                call_id=req.call_id,
                payload=ap.model_dump(mode="json"),
            )
        )
        await self._notify(ap)
        return ap

    async def _notify(self, ap: Approval) -> None:
        amount = f" {ap.currency} {ap.amount:,.2f}" if ap.amount is not None else ""
        link = f"{self.dashboard_url}/approve/{ap.token}"
        body = (
            f"{ap.title}{amount}\n{ap.details}\n"
            f"Caller: {ap.caller or 'unknown'}. Reply within "
            f"{int((ap.expires_at - ap.requested_at).total_seconds() // 60)} min.\n"
            f"Approve or reject: {link}"
        )
        try:
            await self.notifications.dispatch(
                NotificationEvent(
                    tenant_id=ap.tenant_id,
                    company_id=ap.company_id,
                    event=NotifyEvent.APPROVAL_REQUESTED,
                    title=f"Approval needed: {ap.kind.value} — {ap.title}",
                    body=body,
                    context={"approval_id": ap.id, "call_id": ap.call_id, "link": link},
                )
            )
        except Exception:
            log.warning("approval notification failed for %s", ap.id, exc_info=True)

    async def decide(
        self, ap: Approval, approve: bool, by: str, note: str | None = None
    ) -> Approval:
        if ap.status != ApprovalStatus.PENDING:
            raise ValueError(f"approval already {ap.status.value}")
        ap.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        ap.decided_at = datetime.now(UTC)
        ap.decided_by = by
        ap.note = note
        await self.store.put_doc(ap.to_doc())
        if ap.call_id:
            call = self.hub.get(ap.call_id)
            if call is not None and call.pending_approval_id == ap.id:
                call.pending_approval_id = None
        self.hub.publish(
            LiveMessage(
                type="approval.decided",
                tenant_id=ap.tenant_id,
                call_id=ap.call_id,
                payload=ap.model_dump(mode="json"),
            )
        )
        return ap

    async def wait(self, tenant_id: str, approval_id: str, timeout_s: float) -> Approval | None:
        """Long-poll helper for the worker: returns as soon as a decision lands."""
        deadline = asyncio.get_running_loop().time() + timeout_s
        q = self.hub.subscribe(tenant_id)
        try:
            ap = await self.get(tenant_id, approval_id)
            while ap is not None and ap.status == ApprovalStatus.PENDING:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=min(remaining, 5.0))
                except TimeoutError:
                    ap = await self.get(tenant_id, approval_id)
                    continue
                if msg.type == "approval.decided" and msg.payload.get("id") == approval_id:
                    ap = Approval.model_validate(msg.payload)
            return ap
        finally:
            self.hub.unsubscribe(tenant_id, q)


def control_from_json(raw: bytes | str) -> ControlMessage | None:
    try:
        return ControlMessage.model_validate(json.loads(raw))
    except (ValueError, TypeError):
        return None


__all__ = [
    "APPROVAL_KIND",
    "CONTROL_TOPIC",
    "Approval",
    "ApprovalKind",
    "ApprovalRequest",
    "ApprovalService",
    "ApprovalStatus",
    "Command",
    "ControlMessage",
    "JoinInfo",
    "LiveCall",
    "LiveCallHub",
    "LiveKitRoomControl",
    "LiveMessage",
    "RoomControl",
    "SimulatedRoomControl",
    "SupervisorService",
    "control_from_json",
]
