"""Sidebar activity badges: one cheap, tenant-scoped count per navigation item."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from parlio_api.auth import UserDep
from parlio_api.deps import ApprovalsDep, InboxDep, LiveDep, OutboundDep, StoreDep, SupportDep
from parlio_api.live import ApprovalStatus
from parlio_api.outbound import OutboundStatus
from parlio_api.store import TicketStatus

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
