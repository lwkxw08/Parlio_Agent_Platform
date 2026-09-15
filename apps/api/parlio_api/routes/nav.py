"""Sidebar activity badges: one cheap, tenant-scoped count per navigation item."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel

from parlio_api.auth import UserDep
from parlio_api.deps import ApprovalsDep, InboxDep, LiveDep, OutboundDep, StoreDep, SupportDep
from parlio_api.live import ApprovalStatus
from parlio_api.outbound import OutboundStatus
from parlio_api.store import CallFilter, TicketStatus

LOCAL_TZ = ZoneInfo("Europe/London")

router = APIRouter(prefix="/v1/nav", tags=["nav"])


class NavBadges(BaseModel):
    live: int = 0  # active calls + approvals awaiting a decision
    inbox: int = 0  # threads waiting for a human (handed off) or with unread messages
    tickets: int = 0  # open tickets
    transfers: int = 0  # open tickets that asked for a callback
    outbound: int = 0  # queued/retrying outbound calls
    support: int = 0  # Parlio support tickets waiting on the customer's reply
    total: int = 0


@router.get("/badges", response_model=NavBadges)
async def badges(
    user: UserDep,
    tenant_id: str,
    live: LiveDep,
    inbox: InboxDep,
    store: StoreDep,
    approvals: ApprovalsDep,
    outbound: OutboundDep,
    support: SupportDep,
) -> NavBadges:
    user.require_tenant(tenant_id)
    inbox_stats = await inbox.stats(tenant_id)
    open_tickets = await store.list_tickets(tenant_id, TicketStatus.OPEN, limit=5000)
    pending = await approvals.list(tenant_id, ApprovalStatus.PENDING, limit=5000)
    jobs = await outbound.list_calls(tenant_id, limit=5000)
    support_open = await support.tickets(tenant_id, open_only=True)
    b = NavBadges(
        live=len(live.active(tenant_id)) + len(pending),
        inbox=max(inbox_stats.waiting, inbox_stats.unread),
        tickets=len(open_tickets),
        transfers=sum(1 for t in open_tickets if t.callback_window),
        outbound=sum(
            1 for j in jobs if j.status in (OutboundStatus.SCHEDULED, OutboundStatus.RETRY)
        ),
        support=sum(1 for t in support_open if t.status == "waiting_customer"),
    )
    b.total = b.live + b.inbox + b.tickets + b.transfers + b.outbound + b.support
    return b


class Snapshot(BaseModel):
    """What is happening right now, for the Overview page."""

    active_calls: int = 0
    transferring: int = 0  # active calls currently being handed to a person
    waiting_chats: int = 0  # inbox threads waiting for a human
    unread_messages: int = 0
    open_tickets: int = 0
    claimed_tickets: int = 0
    sla_breached: int = 0
    callbacks_due: int = 0  # open tickets that asked for a callback
    approvals_pending: int = 0
    calls_today: int = 0
    answered_today: int = 0
    missed_today: int = 0
    transferred_today: int = 0
    tickets_today: int = 0
    avg_answer_s: float | None = None  # ring → assistant speaking, today
    avg_response_s: float | None = None  # caller stops → assistant replies (p50), today
    generated_at: datetime


def _avg(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


@router.get("/snapshot", response_model=Snapshot)
async def snapshot(
    user: UserDep,
    tenant_id: str,
    live: LiveDep,
    inbox: InboxDep,
    store: StoreDep,
    approvals: ApprovalsDep,
) -> Snapshot:
    user.require_tenant(tenant_id)
    now = datetime.now(UTC)
    start = datetime.combine(now.astimezone(LOCAL_TZ).date(), time.min, LOCAL_TZ).astimezone(UTC)
    active = live.active(tenant_id)
    inbox_stats = await inbox.stats(tenant_id)
    open_t = await store.list_tickets(tenant_id, TicketStatus.OPEN, limit=5000)
    claimed_t = await store.list_tickets(tenant_id, TicketStatus.CLAIMED, limit=5000)
    pending = await approvals.list(tenant_id, ApprovalStatus.PENDING, limit=5000)
    today = await store.filter_calls(CallFilter(tenant_id=tenant_id, since=start, limit=5000))
    finished = [c for c in today if c.status in ("completed", "failed")]
    answered = [c for c in finished if c.answered_at is not None]
    pickups = [c.answer_latency_s for c in answered if c.answer_latency_s is not None]
    turns = [float(p) for c in answered if isinstance(p := c.latency.get("p50_s"), int | float)]
    return Snapshot(
        active_calls=len(active),
        transferring=sum(1 for c in active if c.status == "transferring"),
        waiting_chats=inbox_stats.waiting,
        unread_messages=inbox_stats.unread,
        open_tickets=len(open_t),
        claimed_tickets=len(claimed_t),
        sla_breached=sum(1 for t in [*open_t, *claimed_t] if t.sla_breached),
        callbacks_due=sum(1 for t in open_t if t.callback_window),
        approvals_pending=len(pending),
        calls_today=len(today),
        answered_today=len(answered),
        missed_today=sum(1 for c in finished if c.kind == "missed"),
        transferred_today=sum(1 for c in finished if c.kind == "transferred"),
        tickets_today=sum(1 for c in today if c.ticket_ids),
        avg_answer_s=_avg(pickups),
        avg_response_s=_avg(turns),
        generated_at=now,
    )
