"""Phase 10: live monitoring, supervisor takeover, approvals.

* ``/v1/live/*``            dashboard: active calls, WebSocket stream, join/whisper/take over.
* ``/v1/approvals/*``       dashboard: pending approvals, approve/reject.
* ``/v1/worker/approvals``  voice worker: ask for an approval and long-poll for the decision.
* ``/v1/public/approvals``  tap-to-approve link sent by SMS/Slack/email (one-time token).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep, current_user
from parlio_api.deps import (
    ApprovalsDep,
    AuditDep,
    LiveDep,
    SupervisorDep,
    require_worker_key,
)
from parlio_api.live import (
    Approval,
    ApprovalRequest,
    ApprovalStatus,
    Command,
    JoinInfo,
    LiveCall,
    LiveCallHub,
)
from parlio_api.observability import AuditEntry
from parlio_api.settings import get_settings
from parlio_api.store import CallStore

log = logging.getLogger("parlio.api.live")

router = APIRouter(prefix="/v1/live", tags=["live"])
approvals = APIRouter(prefix="/v1/approvals", tags=["approvals"])
worker = APIRouter(
    prefix="/v1/worker/approvals", tags=["worker"], dependencies=[Depends(require_worker_key)]
)
public = APIRouter(prefix="/v1/public/approvals", tags=["public"])


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


# -- live calls -------------------------------------------------------------------------------


@router.get("/calls", response_model=list[LiveCall])
async def active_calls(user: UserDep, live: LiveDep, tenant_id: str) -> list[LiveCall]:
    user.require_tenant(tenant_id)
    return live.active(tenant_id)


@router.get("/calls/{call_id}", response_model=LiveCall)
async def active_call(user: UserDep, live: LiveDep, call_id: str, tenant_id: str) -> LiveCall:
    user.require_tenant(tenant_id)
    call = live.get(call_id)
    if call is None or call.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call is not active")
    return call


@router.websocket("/ws")
async def live_stream(
    ws: WebSocket,
    tenant_id: str,
    token: str | None = None,
    user: str | None = None,
) -> None:
    """Streams a snapshot then every call event / approval for the tenant.

    Browsers can't set headers on WebSocket upgrades, so credentials come as query params:
    ``token`` (Supabase JWT) or ``user`` (dev-mode email).
    """
    store: CallStore = ws.app.state.store
    live: LiveCallHub = ws.app.state.live
    try:
        principal = await current_user(
            store,
            get_settings(),
            authorization=f"Bearer {token}" if token else None,
            x_parlio_user=user,
        )
        principal.require_tenant(tenant_id)
    except HTTPException as e:
        await ws.close(code=4401 if e.status_code == 401 else 4403, reason=str(e.detail))
        return
    await ws.accept()
    q = live.subscribe(tenant_id)

    async def _pump() -> None:
        while True:
            msg = await q.get()
            await ws.send_text(msg.model_dump_json())

    pump = asyncio.create_task(_pump())
    try:
        while True:
            # client pings keep the connection alive through proxies; payloads are ignored
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("live ws closed", exc_info=True)
    finally:
        pump.cancel()
        live.unsubscribe(tenant_id, q)


class CommandInput(BaseModel):
    cmd: Command
    text: str | None = Field(default=None, max_length=1000)


@router.post("/calls/{call_id}/join", response_model=JoinInfo)
async def join_call(
    request: Request,
    user: UserDep,
    supervisor: SupervisorDep,
    audit: AuditDep,
    call_id: str,
    tenant_id: str,
) -> JoinInfo:
    """Listen in: a subscribe-only LiveKit token for the call's room."""
    user.require_tenant(tenant_id)
    try:
        info = supervisor.join(call_id, tenant_id, user.user_id, user.name or user.email)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    await audit.record(_audit(request, user, tenant_id, "live.listen", call_id))
    return info


@router.post("/calls/{call_id}/command", response_model=JoinInfo | None)
async def command_call(
    request: Request,
    user: UserDep,
    supervisor: SupervisorDep,
    audit: AuditDep,
    call_id: str,
    tenant_id: str,
    body: CommandInput,
) -> JoinInfo | None:
    """whisper / say / takeover / handback / hangup. Takeover returns a publish-capable token."""
    user.require_tenant(tenant_id)
    if body.cmd in (Command.TAKEOVER, Command.HANGUP):
        role = user.role_in(tenant_id)
        if role == "viewer" or role is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "viewers cannot control calls")
    try:
        info = await supervisor.command(
            call_id, tenant_id, user.user_id, user.name or user.email, body.cmd, body.text
        )
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except PermissionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    await audit.record(_audit(request, user, tenant_id, f"live.{body.cmd.value}", call_id))
    return info


@router.post("/calls/{call_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_call(
    user: UserDep, supervisor: SupervisorDep, call_id: str, tenant_id: str
) -> None:
    user.require_tenant(tenant_id)
    supervisor.leave(call_id, tenant_id, user.user_id)


# -- approvals (dashboard) --------------------------------------------------------------------


@approvals.get("", response_model=list[Approval])
async def list_approvals(
    user: UserDep,
    svc: ApprovalsDep,
    tenant_id: str,
    status_: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
) -> list[Approval]:
    user.require_tenant(tenant_id)
    return await svc.list(tenant_id, status_)


class DecisionInput(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


@approvals.post("/{approval_id}/decide", response_model=Approval)
async def decide_approval(
    request: Request,
    user: UserDep,
    svc: ApprovalsDep,
    audit: AuditDep,
    approval_id: str,
    tenant_id: str,
    body: DecisionInput,
) -> Approval:
    user.require_tenant(tenant_id)
    if user.role_in(tenant_id) in ("viewer", None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "viewers cannot control calls")
    ap = await svc.get(tenant_id, approval_id)
    if ap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    try:
        ap = await svc.decide(ap, body.approve, user.email, body.note)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    action = "approval.approve" if body.approve else "approval.reject"
    await audit.record(_audit(request, user, tenant_id, action, approval_id))
    return ap


# -- approvals (worker) -----------------------------------------------------------------------


@worker.post("", response_model=Approval, status_code=status.HTTP_201_CREATED)
async def request_approval(
    svc: ApprovalsDep, tenant_id: str, req: ApprovalRequest, company_id: str | None = None
) -> Approval:
    return await svc.request(tenant_id, company_id, req)


@worker.get("/{approval_id}", response_model=Approval)
async def poll_approval(
    svc: ApprovalsDep,
    approval_id: str,
    tenant_id: str,
    wait_s: Annotated[float, Query(ge=0, le=25)] = 0,
) -> Approval:
    """Returns the approval; with ``wait_s`` it long-polls until decided or the wait elapses."""
    ap = (
        await svc.wait(tenant_id, approval_id, wait_s)
        if wait_s
        else await svc.get(tenant_id, approval_id)
    )
    if ap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "approval not found")
    return ap


# -- approvals (public tap-to-approve) --------------------------------------------------------


@public.get("/{token}")
async def public_approval(svc: ApprovalsDep, token: str) -> dict[str, Any]:
    ap = await svc.by_token(token)
    if ap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link is invalid or has expired")
    return ap.public()


class PublicDecision(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=100)


@public.post("/{token}")
async def public_decide(svc: ApprovalsDep, token: str, body: PublicDecision) -> dict[str, Any]:
    ap = await svc.by_token(token)
    if ap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "link is invalid or has expired")
    try:
        ap = await svc.decide(ap, body.approve, body.name or "link", body.note)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return ap.public()
