"""Phase 17/18 routes: tenant Health page, admin Ops tab, public status page, Support desk."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.admin import PLATFORM_TENANT
from parlio_api.auth import UserDep, current_user
from parlio_api.deps import AuditDep, OpsDep, SupportDep
from parlio_api.ops import (
    FailoverState,
    FaultReport,
    ForwardingHealth,
    Incident,
    IncidentUpdate,
    OnCallConfig,
    OpsAlert,
    StatusPage,
    SyntheticRun,
    TenantHealth,
    TrunkHealth,
)
from parlio_api.routes.admin import StaffDep, _audit, _fail
from parlio_api.support import (
    Priority,
    SupportStatus,
    SupportTicket,
    SupportTicketIn,
    TagReview,
    TenantStatus,
    kb_search,
)

router = APIRouter(prefix="/v1", tags=["health"], dependencies=[Depends(current_user)])
admin = APIRouter(prefix="/v1/admin/ops", tags=["admin-ops"])
public = APIRouter(prefix="/v1/public", tags=["public"])


# -- tenant Health page ----------------------------------------------------------------------------


class HealthView(BaseModel):
    health: TenantHealth
    forwarding: ForwardingHealth
    trunks: list[TrunkHealth]
    alerts: list[OpsAlert]
    synthetic: list[SyntheticRun]
    faults: list[FaultReport]


@router.get("/health/overview", response_model=HealthView)
async def health_overview(user: UserDep, ops: OpsDep, tenant_id: str) -> HealthView:
    user.require_tenant(tenant_id)
    return HealthView(
        health=await ops.tenant_health(tenant_id),
        forwarding=await ops.forwarding_health(tenant_id),
        trunks=await ops.trunk_health(tenant_id),
        alerts=await ops.alerts(tenant_id, open_only=True),
        synthetic=await ops.synthetic_runs(tenant_id, limit=20),
        faults=await ops.fault_reports(tenant_id),
    )


@router.post("/health/synthetic", response_model=SyntheticRun)
async def run_synthetic(user: UserDep, ops: OpsDep, tenant_id: str) -> SyntheticRun:
    user.require_tenant(tenant_id)
    try:
        return await ops.synthetic_call(tenant_id, trigger="manual")
    except ValueError as e:
        raise _fail(e) from e


@router.post("/health/calls/{call_id}/diagnose", response_model=FaultReport)
async def diagnose_call(user: UserDep, ops: OpsDep, tenant_id: str, call_id: str) -> FaultReport:
    user.require_tenant(tenant_id)
    rep = await ops.classify_call(tenant_id, call_id)
    if rep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    return rep


@router.post("/health/trunks/{trunk_id}/diagnose", response_model=FaultReport)
async def diagnose_trunk(user: UserDep, ops: OpsDep, tenant_id: str, trunk_id: str) -> FaultReport:
    user.require_tenant(tenant_id)
    rep = await ops.classify_trunk(tenant_id, trunk_id)
    if rep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trunk not found")
    return rep


@router.post("/health/forwarding/diagnose", response_model=FaultReport)
async def diagnose_forwarding(user: UserDep, ops: OpsDep, tenant_id: str) -> FaultReport:
    user.require_tenant(tenant_id)
    return await ops.classify_forwarding(tenant_id)


# -- tenant Support page ---------------------------------------------------------------------------


@router.get("/support/tickets", response_model=list[SupportTicket])
async def my_tickets(user: UserDep, desk: SupportDep, tenant_id: str) -> list[SupportTicket]:
    user.require_tenant(tenant_id)
    return [t.customer_view() for t in await desk.tickets(tenant_id)]


@router.post("/support/tickets", response_model=SupportTicket, status_code=status.HTTP_201_CREATED)
async def open_ticket(
    user: UserDep, desk: SupportDep, tenant_id: str, body: SupportTicketIn
) -> SupportTicket:
    user.require_tenant(tenant_id)
    return (await desk.create_ticket(tenant_id, user.email, body)).customer_view()


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


async def _own_ticket(desk: SupportDep, tenant_id: str, ticket_id: str) -> SupportTicket:
    t = await desk.get(ticket_id, tenant_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return t


@router.post("/support/tickets/{ticket_id}/reply", response_model=SupportTicket)
async def reply_ticket(
    user: UserDep, desk: SupportDep, tenant_id: str, ticket_id: str, body: ReplyIn
) -> SupportTicket:
    user.require_tenant(tenant_id)
    t = await _own_ticket(desk, tenant_id, ticket_id)
    return (await desk.reply(t, user.email, body.text, staff=False)).customer_view()


class CsatIn(BaseModel):
    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/support/tickets/{ticket_id}/csat", response_model=SupportTicket)
async def csat(
    user: UserDep, desk: SupportDep, tenant_id: str, ticket_id: str, body: CsatIn
) -> SupportTicket:
    user.require_tenant(tenant_id)
    t = await _own_ticket(desk, tenant_id, ticket_id)
    return (await desk.csat(t, body.score, body.comment)).customer_view()


@router.get("/support/kb")
async def kb(user: UserDep, desk: SupportDep, q: str = "") -> list[dict[str, Any]]:
    return [a.model_dump(mode="json") for a in kb_search(q)]


@router.get("/support/walkthrough")
async def walkthrough(
    user: UserDep,
    desk: SupportDep,
    kind: Literal["forwarding", "sip"] = "forwarding",
    provider: str | None = None,
) -> dict[str, Any]:
    if kind == "sip":
        return desk.walkthrough_sip(provider)
    return desk.walkthrough_forwarding(provider)


# -- admin Ops tab ---------------------------------------------------------------------------------


class OpsOverview(BaseModel):
    board: list[TenantHealth]
    alerts: dict[str, int]
    open_alerts: list[OpsAlert]
    status: StatusPage
    oncall: OnCallConfig
    failover: FailoverState
    canary_ok: bool
    canary_reasons: list[str]
    open_tickets: int
    breached_tickets: int


@admin.get("/overview", response_model=OpsOverview)
async def ops_overview(user: StaffDep, ops: OpsDep, desk: SupportDep) -> OpsOverview:
    ok, reasons = await ops.canary_ok()
    tickets = await desk.tickets(open_only=True)
    return OpsOverview(
        board=await ops.health_board(),
        alerts=await ops.alert_summary(),
        open_alerts=await ops.alerts(open_only=True),
        status=await ops.status_page(),
        oncall=await ops.oncall(),
        failover=await ops.failover(),
        canary_ok=ok,
        canary_reasons=reasons,
        open_tickets=len(tickets),
        breached_tickets=sum(1 for t in tickets if t.sla_breached),
    )


@admin.get("/tenants/{tenant_id}", response_model=HealthView)
async def ops_tenant(user: StaffDep, ops: OpsDep, tenant_id: str) -> HealthView:
    return HealthView(
        health=await ops.tenant_health(tenant_id),
        forwarding=await ops.forwarding_health(tenant_id),
        trunks=await ops.trunk_health(tenant_id),
        alerts=await ops.alerts(tenant_id),
        synthetic=await ops.synthetic_runs(tenant_id, limit=20),
        faults=await ops.fault_reports(tenant_id),
    )


@admin.post("/sweep")
async def ops_sweep(
    user: StaffDep, ops: OpsDep, request: Request, audit: AuditDep, synthetic: bool = False
) -> dict[str, int]:
    user.require_staff("support")
    out = await ops.sweep(synthetic=synthetic)
    await _audit(audit, request, user, PLATFORM_TENANT, "admin.ops.sweep", meta=out)
    return out


@admin.post("/synthetic", response_model=list[SyntheticRun])
async def ops_synthetic_all(
    user: StaffDep,
    ops: OpsDep,
    request: Request,
    audit: AuditDep,
    trigger: Literal["daily", "post_deploy", "manual"] = "manual",
) -> list[SyntheticRun]:
    user.require_staff("support")
    out = await ops.run_all(trigger)
    await _audit(
        audit, request, user, PLATFORM_TENANT, "admin.ops.synthetic", meta={"runs": len(out)}
    )
    return out


@admin.post("/synthetic/{tenant_id}", response_model=SyntheticRun)
async def ops_synthetic_one(
    user: StaffDep, ops: OpsDep, request: Request, audit: AuditDep, tenant_id: str
) -> SyntheticRun:
    user.require_staff("support")
    try:
        run = await ops.synthetic_call(tenant_id, trigger="support")
    except ValueError as e:
        raise _fail(e) from e
    await _audit(audit, request, user, tenant_id, "admin.ops.synthetic", target=run.id)
    return run


@admin.post("/alerts/{alert_id}/ack", response_model=OpsAlert)
async def ack_alert(
    user: StaffDep, ops: OpsDep, request: Request, audit: AuditDep, alert_id: str
) -> OpsAlert:
    user.require_staff("support")
    a = await ops.acknowledge(alert_id, user.email)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    await _audit(audit, request, user, a.tenant_id, "admin.ops.alert.ack", target=alert_id)
    return a


@admin.post("/alerts/{alert_id}/resolve", response_model=OpsAlert)
async def resolve_alert(
    user: StaffDep, ops: OpsDep, request: Request, audit: AuditDep, alert_id: str
) -> OpsAlert:
    user.require_staff("support")
    a = await ops.resolve_alert(alert_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    await _audit(audit, request, user, a.tenant_id, "admin.ops.alert.resolve", target=alert_id)
    return a


@admin.post("/trunks/{tenant_id}/{trunk_id}/remediate")
async def remediate_trunk(
    user: StaffDep, ops: OpsDep, request: Request, audit: AuditDep, tenant_id: str, trunk_id: str
) -> dict[str, Any]:
    user.require_staff("support")
    trunk = await ops.remediate_trunk(tenant_id, trunk_id)
    if trunk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trunk not found")
    await _audit(audit, request, user, tenant_id, "admin.ops.trunk.remediate", target=trunk_id)
    return {"trunk_id": trunk.id, "status": trunk.status}


@admin.get("/incidents", response_model=list[Incident])
async def incidents(user: StaffDep, ops: OpsDep) -> list[Incident]:
    return await ops.incidents()


@admin.put("/incidents/{incident_id}", response_model=Incident)
async def save_incident(
    user: StaffDep, ops: OpsDep, request: Request, audit: AuditDep, incident_id: str, body: Incident
) -> Incident:
    user.require_staff("support")
    if body.id != incident_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "incident id mismatch")
    body.created_by = body.created_by or user.email
    inc = await ops.save_incident(body)
    await _audit(audit, request, user, PLATFORM_TENANT, "admin.ops.incident.save", target=inc.id)
    return inc


@admin.post("/incidents/{incident_id}/updates", response_model=Incident)
async def incident_update(
    user: StaffDep,
    ops: OpsDep,
    request: Request,
    audit: AuditDep,
    incident_id: str,
    body: IncidentUpdate,
) -> Incident:
    user.require_staff("support")
    body.by = user.email
    inc = await ops.add_incident_update(incident_id, body)
    if inc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    await _audit(
        audit, request, user, PLATFORM_TENANT, "admin.ops.incident.update", target=incident_id
    )
    return inc


@admin.put("/oncall", response_model=OnCallConfig)
async def save_oncall(
    user: StaffDep, ops: OpsDep, request: Request, audit: AuditDep, body: OnCallConfig
) -> OnCallConfig:
    user.require_staff("owner")
    out = await ops.save_oncall(body, user.email)
    await _audit(audit, request, user, PLATFORM_TENANT, "admin.ops.oncall.save")
    return out


class FailoverIn(BaseModel):
    to: str
    reason: str = Field(min_length=1, max_length=500)


@admin.post("/failover", response_model=FailoverState)
async def failover(
    user: StaffDep, ops: OpsDep, request: Request, audit: AuditDep, body: FailoverIn
) -> FailoverState:
    user.require_staff("owner")
    try:
        out = await ops.switch_carrier(body.to, body.reason, user.email)
    except ValueError as e:
        raise _fail(e) from e
    await _audit(
        audit,
        request,
        user,
        PLATFORM_TENANT,
        "admin.ops.failover",
        meta={"to": body.to, "reason": body.reason},
    )
    return out


# -- admin support desk ----------------------------------------------------------------------------


@admin.get("/support/tickets", response_model=list[SupportTicket])
async def desk_tickets(
    user: StaffDep, desk: SupportDep, tenant_id: str | None = None, open_only: bool = False
) -> list[SupportTicket]:
    return await desk.tickets(tenant_id, open_only=open_only)


@admin.get("/support/tenants/{tenant_id}/status", response_model=TenantStatus)
async def desk_tenant_status(user: StaffDep, desk: SupportDep, tenant_id: str) -> TenantStatus:
    return await desk.tenant_status(tenant_id)


async def _ticket(desk: SupportDep, ticket_id: str) -> SupportTicket:
    t = await desk.get(ticket_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket not found")
    return t


class StaffReplyIn(ReplyIn):
    public: bool = True


@admin.post("/support/tickets/{ticket_id}/reply", response_model=SupportTicket)
async def desk_reply(
    user: StaffDep,
    desk: SupportDep,
    request: Request,
    audit: AuditDep,
    ticket_id: str,
    body: StaffReplyIn,
) -> SupportTicket:
    user.require_staff("support")
    t = await _ticket(desk, ticket_id)
    out = await desk.reply(t, user.email, body.text, staff=True, public=body.public)
    await _audit(audit, request, user, t.tenant_id, "admin.support.reply", target=ticket_id)
    return out


class StatusIn(BaseModel):
    status: SupportStatus
    assignee: str | None = None
    priority: Priority | None = None
    tags: list[str] | None = None


@admin.post("/support/tickets/{ticket_id}/status", response_model=SupportTicket)
async def desk_status(
    user: StaffDep,
    desk: SupportDep,
    request: Request,
    audit: AuditDep,
    ticket_id: str,
    body: StatusIn,
) -> SupportTicket:
    user.require_staff("support")
    t = await _ticket(desk, ticket_id)
    out = await desk.set_status(
        t, body.status, user.email, assignee=body.assignee, priority=body.priority, tags=body.tags
    )
    await _audit(
        audit,
        request,
        user,
        t.tenant_id,
        "admin.support.status",
        target=ticket_id,
        meta={"status": body.status},
    )
    return out


class EscalateIn(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)


@admin.post("/support/tickets/{ticket_id}/escalate", response_model=SupportTicket)
async def desk_escalate(
    user: StaffDep,
    desk: SupportDep,
    request: Request,
    audit: AuditDep,
    ticket_id: str,
    body: EscalateIn,
) -> SupportTicket:
    user.require_staff("support")
    t = await _ticket(desk, ticket_id)
    out = await desk.escalate_engineering(t, user.email, body.summary)
    await _audit(audit, request, user, t.tenant_id, "admin.support.escalate", target=ticket_id)
    return out


class ProviderEmailIn(BaseModel):
    provider_email: str = Field(min_length=3, max_length=200)
    consent: bool = False


@admin.post("/support/tickets/{ticket_id}/provider-email", response_model=SupportTicket)
async def desk_provider_email(
    user: StaffDep,
    desk: SupportDep,
    request: Request,
    audit: AuditDep,
    ticket_id: str,
    body: ProviderEmailIn,
) -> SupportTicket:
    user.require_staff("support")
    t = await _ticket(desk, ticket_id)
    try:
        out = await desk.email_provider(t, body.provider_email, user.email, body.consent)
    except ValueError as e:
        raise _fail(e) from e
    await _audit(
        audit,
        request,
        user,
        t.tenant_id,
        "admin.support.provider_email",
        target=ticket_id,
        meta={"provider_email": body.provider_email, "consent": body.consent},
    )
    return out


@admin.post("/support/tenants/{tenant_id}/sip-diagnostics", response_model=list[TrunkHealth])
async def desk_sip_diagnostics(
    user: StaffDep,
    desk: SupportDep,
    request: Request,
    audit: AuditDep,
    tenant_id: str,
    trunk_id: str | None = None,
) -> list[TrunkHealth]:
    user.require_staff("support")
    out = await desk.run_sip_diagnostics(tenant_id, trunk_id)
    await _audit(audit, request, user, tenant_id, "admin.support.sip_diagnostics", target=trunk_id)
    return out


@admin.post("/support/sla-sweep", response_model=list[SupportTicket])
async def desk_sla_sweep(user: StaffDep, desk: SupportDep) -> list[SupportTicket]:
    user.require_staff("support")
    return await desk.sweep_sla()


@admin.get("/support/tag-review", response_model=TagReview)
async def desk_tag_review(user: StaffDep, desk: SupportDep, days: int = 7) -> TagReview:
    return await desk.tag_review(days)


# -- public status page ----------------------------------------------------------------------------


@public.get("/status-page", response_model=StatusPage)
async def public_status(ops: OpsDep) -> StatusPage:
    return await ops.status_page()
