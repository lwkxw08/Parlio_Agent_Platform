"""Phase 21b/21c dashboard routes: AI business advisor, insights CSV export, scheduled reports."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from parlio_api.advisor import (
    AdvisorOverview,
    AdvisorRun,
    AdvisorSettings,
    Recommendation,
)
from parlio_api.auth import UserDep
from parlio_api.deps import AdvisorDep, AuditDep, ReportsDep, StoreDep, ValueDep
from parlio_api.insights import build_insights, load_inputs
from parlio_api.observability import AuditEntry
from parlio_api.reports import SECTIONS, ReportSchedule, ReportSent, insights_csv

router = APIRouter(prefix="/v1", tags=["advisor"])


def _tid(user: UserDep, tenant_id: str | None) -> str:
    tid = tenant_id or (user.tenant_ids[0] if user.tenant_ids else None)
    if tid is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no organisation")
    user.require_tenant(tid)
    return tid


async def _gate(advisor: AdvisorDep, tid: str) -> None:
    if not await advisor.entitled(tid):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "AI business advisor is available on Growth and above"
        )


# -- advisor ------------------------------------------------------------------------------------


@router.get("/advisor", response_model=AdvisorOverview)
async def advisor_overview(
    user: UserDep, advisor: AdvisorDep, tenant_id: str | None = None
) -> AdvisorOverview:
    tid = _tid(user, tenant_id)
    await _gate(advisor, tid)
    return await advisor.overview(tid)


@router.post("/advisor/run", response_model=AdvisorRun)
async def advisor_run(
    request: Request,
    user: UserDep,
    advisor: AdvisorDep,
    audit: AuditDep,
    tenant_id: str | None = None,
) -> AdvisorRun:
    tid = _tid(user, tenant_id)
    await _gate(advisor, tid)
    run = await advisor.run(tid, trigger="manual")
    await audit.record(
        AuditEntry(
            tenant_id=tid,
            actor=user.email,
            action="advisor.run",
            target=run.id,
            ip=request.client.host if request.client else None,
            meta={"generated": run.generated, "llm_pence": run.llm_pence},
        )
    )
    return run


@router.put("/advisor/settings", response_model=AdvisorSettings)
async def advisor_settings(
    body: AdvisorSettings,
    request: Request,
    user: UserDep,
    advisor: AdvisorDep,
    audit: AuditDep,
    tenant_id: str | None = None,
) -> AdvisorSettings:
    tid = _tid(user, tenant_id)
    await _gate(advisor, tid)
    saved = await advisor.save_settings(tid, body)
    await audit.record(
        AuditEntry(
            tenant_id=tid,
            actor=user.email,
            action="advisor.settings_updated",
            ip=request.client.host if request.client else None,
            meta=saved.model_dump(mode="json", exclude={"tenant_id", "updated_at"}),
        )
    )
    return saved


class ApplyBody(BaseModel):
    action_index: int = Field(0, ge=0)
    text: str | None = Field(None, max_length=2000)


class SnoozeBody(BaseModel):
    days: int = Field(14, ge=1, le=90)


async def _lifecycle(
    request: Request,
    user: UserDep,
    audit: AuditDep,
    tid: str,
    rec: Recommendation | None,
    action: str,
) -> Recommendation:
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recommendation not found")
    await audit.record(
        AuditEntry(
            tenant_id=tid,
            actor=user.email,
            action=f"advisor.{action}",
            target=rec.id,
            ip=request.client.host if request.client else None,
            meta={"rule": rec.rule},
        )
    )
    return rec


@router.post("/advisor/{rec_id}/apply", response_model=Recommendation)
async def advisor_apply(
    rec_id: str,
    body: ApplyBody,
    request: Request,
    user: UserDep,
    advisor: AdvisorDep,
    audit: AuditDep,
    tenant_id: str | None = None,
) -> Recommendation:
    tid = _tid(user, tenant_id)
    await _gate(advisor, tid)
    try:
        rec = await advisor.apply(
            tid, rec_id, actor=user.email, action_index=body.action_index, text=body.text
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return await _lifecycle(request, user, audit, tid, rec, "apply")


@router.post("/advisor/{rec_id}/dismiss", response_model=Recommendation)
async def advisor_dismiss(
    rec_id: str,
    request: Request,
    user: UserDep,
    advisor: AdvisorDep,
    audit: AuditDep,
    tenant_id: str | None = None,
) -> Recommendation:
    tid = _tid(user, tenant_id)
    await _gate(advisor, tid)
    return await _lifecycle(
        request, user, audit, tid, await advisor.dismiss(tid, rec_id), "dismiss"
    )


@router.post("/advisor/{rec_id}/snooze", response_model=Recommendation)
async def advisor_snooze(
    rec_id: str,
    body: SnoozeBody,
    request: Request,
    user: UserDep,
    advisor: AdvisorDep,
    audit: AuditDep,
    tenant_id: str | None = None,
) -> Recommendation:
    tid = _tid(user, tenant_id)
    await _gate(advisor, tid)
    rec = await advisor.snooze(tid, rec_id, days=body.days)
    return await _lifecycle(request, user, audit, tid, rec, "snooze")


# -- CSV export -----------------------------------------------------------------------------------


@router.get("/analytics/insights.csv")
async def insights_export(
    store: StoreDep,
    value: ValueDep,
    user: UserDep,
    tenant_id: str | None = None,
    section: str = "summary",
    days: Annotated[int, Query(ge=7, le=730)] = 30,
    timezone: str = "Europe/London",
) -> Response:
    tid = _tid(user, tenant_id)
    if section not in SECTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown section '{section}'")
    inp = await load_inputs(store, value, tid)
    report = build_insights(inp, days=days, timezone=timezone, now=datetime.now(UTC))
    fname = f"parlio-insights-{section}-{days}d-{report.end:%Y%m%d}.csv"
    return Response(
        insights_csv(report, section),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# -- scheduled reports ---------------------------------------------------------------------------


class ReportOverview(BaseModel):
    schedule: ReportSchedule
    history: list[ReportSent]
    sections: list[str] = Field(default_factory=lambda: list(SECTIONS))


@router.get("/analytics/reports", response_model=ReportOverview)
async def reports_overview(
    user: UserDep, reports: ReportsDep, tenant_id: str | None = None
) -> ReportOverview:
    tid = _tid(user, tenant_id)
    return ReportOverview(schedule=await reports.schedule(tid), history=await reports.history(tid))


@router.put("/analytics/reports/schedule", response_model=ReportSchedule)
async def reports_schedule(
    body: ReportSchedule,
    request: Request,
    user: UserDep,
    reports: ReportsDep,
    audit: AuditDep,
    tenant_id: str | None = None,
) -> ReportSchedule:
    tid = _tid(user, tenant_id)
    try:
        saved = await reports.save_schedule(tid, body)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    await audit.record(
        AuditEntry(
            tenant_id=tid,
            actor=user.email,
            action="analytics.report_schedule_updated",
            ip=request.client.host if request.client else None,
            meta=saved.model_dump(mode="json", exclude={"tenant_id", "updated_at"}),
        )
    )
    return saved


@router.post("/analytics/reports/send", response_model=ReportSent)
async def reports_send_now(
    user: UserDep,
    reports: ReportsDep,
    tenant_id: str | None = None,
    days: Annotated[int, Query(ge=7, le=365)] = 7,
) -> ReportSent:
    tid = _tid(user, tenant_id)
    return await reports.send(tid, days=days)
