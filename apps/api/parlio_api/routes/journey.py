"""Phase 19 — guided sign-up, setup checklist, call explainability, trust centre.

* ``POST /v1/onboarding/recommend``  questionnaire -> recommended plan + comparison (no tenant yet).
* ``GET  /v1/onboarding/verticals``  playbooks the wizard offers.
* ``GET  /v1/setup/checklist``       live checklist for a tenant.
* ``GET  /v1/calls/{id}/explain``    "why did the AI say this?" evidence per assistant turn.
* ``POST /v1/setup/checkins/sweep``  run the 7/30-day check-in sweep now (owners; also on a timer).
* ``GET  /v1/public/trust``          trust centre content.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from parlio_api.auth import UserDep
from parlio_api.billing import PLANS
from parlio_api.deps import (
    BillingDep,
    CalendarDep,
    NotificationsDep,
    SettingsDep,
    SipDep,
    StoreDep,
)
from parlio_api.journey import (
    QUESTIONNAIRE_KIND,
    VERTICALS,
    CallExplanation,
    CheckInLoop,
    PlanRecommendation,
    Questionnaire,
    SetupChecklist,
    TrustCentre,
    VerticalPlaybook,
    explain_call,
    recommend_plan,
    setup_checklist,
    trust_centre,
)

router = APIRouter(prefix="/v1", tags=["journey"])
public = APIRouter(prefix="/v1/public", tags=["public"])


@router.post("/onboarding/recommend", response_model=PlanRecommendation)
async def recommend(
    body: Questionnaire, _user: UserDep, settings: SettingsDep
) -> PlanRecommendation:
    return recommend_plan(body, PLANS, settings.trial_days)


@router.get("/onboarding/verticals", response_model=list[VerticalPlaybook])
async def verticals(_user: UserDep) -> list[VerticalPlaybook]:
    return VERTICALS


@router.get("/setup/checklist", response_model=SetupChecklist)
async def checklist(
    tenant_id: str,
    user: UserDep,
    store: StoreDep,
    billing: BillingDep,
    sip: SipDep,
    calendar: CalendarDep,
    notifications: NotificationsDep,
) -> SetupChecklist:
    user.require_tenant(tenant_id)
    return await setup_checklist(tenant_id, store, billing, sip, calendar, notifications)


class QuestionnaireOut(BaseModel):
    tenant_id: str
    questionnaire: Questionnaire | None
    recommended_plan_id: str | None


@router.get("/setup/questionnaire", response_model=QuestionnaireOut)
async def questionnaire(tenant_id: str, user: UserDep, store: StoreDep) -> QuestionnaireOut:
    user.require_tenant(tenant_id)
    doc = await store.get_doc(QUESTIONNAIRE_KIND, tenant_id)
    if doc is None:
        return QuestionnaireOut(tenant_id=tenant_id, questionnaire=None, recommended_plan_id=None)
    return QuestionnaireOut(
        tenant_id=tenant_id,
        questionnaire=Questionnaire.model_validate(doc.data.get("questionnaire") or {}),
        recommended_plan_id=doc.data.get("recommended_plan_id"),
    )


@router.get("/calls/{call_id}/explain", response_model=CallExplanation)
async def explain(call_id: str, user: UserDep, store: StoreDep) -> CallExplanation:
    call = await store.get_call(call_id)
    if call is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    user.require_tenant(call.tenant_id)
    cfg = None
    versions = await store.list_assistant_versions(call.assistant_id)
    live = [v for v in versions if v.created_at <= call.started_at]
    if live:
        cfg = max(live, key=lambda v: v.version).config
    if cfg is None:
        cfg = await store.get_assistant(call.assistant_id)
    return explain_call(call, cfg)


class SweepResult(BaseModel):
    sent: list[str]


@router.post("/setup/checkins/sweep", response_model=SweepResult)
async def sweep(
    tenant_id: str,
    user: UserDep,
    store: StoreDep,
    billing: BillingDep,
    sip: SipDep,
    calendar: CalendarDep,
    notifications: NotificationsDep,
) -> SweepResult:
    user.require_tenant(tenant_id)
    loop = CheckInLoop(store, billing, sip, calendar, notifications)
    sent = await loop.sweep()
    return SweepResult(sent=[s for s in sent if s.startswith(f"{tenant_id}:")])


@public.get("/trust", response_model=TrustCentre)
async def trust(settings: SettingsDep) -> TrustCentre:
    return trust_centre(settings.dashboard_url)
