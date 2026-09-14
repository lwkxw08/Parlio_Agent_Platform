"""Phase 13 routes: QA scores & insights, simulation sandbox, voice cloning."""

from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from parlio_api.auth import UserDep
from parlio_api.deps import AuditDep, QADep, SimulationDep, StoreDep, VoiceCloneDep
from parlio_api.observability import AuditEntry
from parlio_api.qa import (
    CONSENT_STATEMENT,
    Insight,
    InsightStatus,
    QAScore,
    QASettings,
    QAStats,
    Scenario,
    SimulationRun,
    VoiceClone,
)
from parlio_voice.models import AssistantConfig

router = APIRouter(prefix="/v1/quality", tags=["quality"])


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


# -- QA scores -------------------------------------------------------------------------------------


class QAOverview(BaseModel):
    settings: QASettings
    stats: QAStats
    recent: list[QAScore]
    insights: list[Insight]


@router.get("/overview", response_model=QAOverview)
async def overview(user: UserDep, qa: QADep, tenant_id: str, limit: int = 50) -> QAOverview:
    user.require_tenant(tenant_id)
    return QAOverview(
        settings=await qa.settings(tenant_id),
        stats=await qa.stats(tenant_id),
        recent=await qa.list_scores(tenant_id, min(max(limit, 1), 500)),
        insights=await qa.list_insights(tenant_id, InsightStatus.OPEN),
    )


class QASettingsInput(BaseModel):
    enabled: bool = True
    alert_below: int = Field(5, ge=0, le=10)
    alert_on_hallucination: bool = True
    min_turns: int = Field(2, ge=0, le=20)


@router.put("/settings", response_model=QASettings)
async def save_settings(
    body: QASettingsInput, user: UserDep, qa: QADep, tenant_id: str
) -> QASettings:
    user.require_admin(tenant_id)
    return await qa.save_settings(QASettings(tenant_id=tenant_id, **body.model_dump()))


@router.get("/calls/{call_id}", response_model=QAScore)
async def call_score(user: UserDep, qa: QADep, tenant_id: str, call_id: str) -> QAScore:
    user.require_tenant(tenant_id)
    s = await qa.get_score(tenant_id, call_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not scored")
    return s


@router.post("/calls/{call_id}/rescore", response_model=QAScore)
async def rescore(
    user: UserDep, qa: QADep, store: StoreDep, tenant_id: str, call_id: str
) -> QAScore:
    user.require_tenant(tenant_id)
    call = await store.get_call(call_id)
    if call is None or call.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "call not found")
    s = await qa.score_call(call, force=True)
    if s is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "call cannot be scored (not answered)")
    return s


# -- insights --------------------------------------------------------------------------------------


@router.get("/insights", response_model=list[Insight])
async def insights(
    user: UserDep, qa: QADep, tenant_id: str, status_: InsightStatus | None = None
) -> list[Insight]:
    user.require_tenant(tenant_id)
    return await qa.list_insights(tenant_id, status_)


@router.post("/insights/rebuild", response_model=list[Insight])
async def rebuild(user: UserDep, qa: QADep, tenant_id: str) -> list[Insight]:
    user.require_admin(tenant_id)
    return await qa.rebuild_insights(tenant_id)


class ApplyInsight(BaseModel):
    question: str | None = Field(None, max_length=300)
    answer: str | None = Field(None, max_length=2000)
    rule: str | None = Field(None, max_length=2000)


@router.post("/insights/{insight_id}/apply", response_model=Insight)
async def apply_insight(
    insight_id: str,
    body: ApplyInsight,
    request: Request,
    user: UserDep,
    qa: QADep,
    audit: AuditDep,
    tenant_id: str,
) -> Insight:
    user.require_admin(tenant_id)
    try:
        ins = await qa.apply_insight(
            tenant_id, insight_id, answer=body.answer, rule=body.rule, question=body.question
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "insight not found")
    await audit.record(_audit(request, user, tenant_id, "quality.insight.apply", insight_id))
    return ins


@router.post("/insights/{insight_id}/dismiss", response_model=Insight)
async def dismiss_insight(insight_id: str, user: UserDep, qa: QADep, tenant_id: str) -> Insight:
    user.require_admin(tenant_id)
    ins = await qa.dismiss_insight(tenant_id, insight_id)
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "insight not found")
    return ins


# -- simulation ------------------------------------------------------------------------------------


@router.get("/scenarios", response_model=list[Scenario])
async def scenarios(user: UserDep, sim: SimulationDep, tenant_id: str) -> list[Scenario]:
    user.require_tenant(tenant_id)
    return await sim.scenarios(tenant_id)


class ScenarioInput(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    persona: str = "A polite first-time caller"
    goal: str = ""
    turns: list[str] = Field(min_length=1, max_length=20)
    expect: dict[str, object] = Field(default_factory=dict)


@router.put("/scenarios", response_model=Scenario)
async def save_scenario(
    body: ScenarioInput, user: UserDep, sim: SimulationDep, tenant_id: str
) -> Scenario:
    user.require_admin(tenant_id)
    data = body.model_dump(exclude={"id"})
    sc = Scenario(tenant_id=tenant_id, **({"id": body.id} if body.id else {}), **data)
    if body.id:
        existing = {s.id: s for s in await sim.scenarios(tenant_id)}
        if body.id not in existing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "scenario not found")
    return await sim.save_scenario(sc)


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scenario(
    scenario_id: str, user: UserDep, sim: SimulationDep, tenant_id: str
) -> None:
    user.require_admin(tenant_id)
    if not await sim.delete_scenario(tenant_id, scenario_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scenario not found")


class RunInput(BaseModel):
    assistant_id: str
    scenario_ids: list[str] = Field(default_factory=list)
    draft: AssistantConfig | None = Field(None, description="unsaved config to test as variant A")
    variant_b: AssistantConfig | None = Field(None, description="second config for A/B")


@router.post("/simulate", response_model=SimulationRun)
async def simulate(
    body: RunInput, user: UserDep, sim: SimulationDep, tenant_id: str
) -> SimulationRun:
    user.require_tenant(tenant_id)
    try:
        return await sim.run(
            tenant_id,
            body.assistant_id,
            body.scenario_ids,
            draft=body.draft,
            variant_b=body.variant_b,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.get("/simulate/runs", response_model=list[SimulationRun])
async def runs(
    user: UserDep, sim: SimulationDep, tenant_id: str, limit: int = 20
) -> list[SimulationRun]:
    user.require_tenant(tenant_id)
    return await sim.runs(tenant_id, min(max(limit, 1), 100))


# -- voice cloning ---------------------------------------------------------------------------------


class VoiceCloneView(BaseModel):
    consent_statement: str
    provider: str
    live: bool
    clones: list[VoiceClone]


@router.get("/voice-clones", response_model=VoiceCloneView)
async def voice_clones(user: UserDep, clones: VoiceCloneDep, tenant_id: str) -> VoiceCloneView:
    user.require_tenant(tenant_id)
    return VoiceCloneView(
        consent_statement=CONSENT_STATEMENT,
        provider=clones.provider.name,
        live=clones.provider.name != "simulated",
        clones=await clones.list(tenant_id),
    )


class VoiceCloneInput(BaseModel):
    assistant_id: str
    name: str = Field(min_length=1, max_length=60)
    consent: bool
    sample_base64: str = Field(min_length=16, description="audio sample (wav/mp3), base64")
    sample_seconds: float = Field(gt=0)


@router.post("/voice-clones", response_model=VoiceClone, status_code=status.HTTP_201_CREATED)
async def create_clone(
    body: VoiceCloneInput,
    request: Request,
    user: UserDep,
    clones: VoiceCloneDep,
    store: StoreDep,
    audit: AuditDep,
    tenant_id: str,
) -> VoiceClone:
    user.require_admin(tenant_id)
    cfg = await store.get_assistant(body.assistant_id)
    if cfg is None or cfg.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assistant not found")
    try:
        sample = base64.b64decode(body.sample_base64, validate=True)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sample is not valid base64") from e
    try:
        clone = await clones.request(
            cfg,
            name=body.name,
            consent=body.consent,
            consent_by=user.email,
            sample=sample,
            sample_seconds=body.sample_seconds,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await audit.record(_audit(request, user, tenant_id, "quality.voice_clone.create", clone.id))
    return clone


@router.post("/voice-clones/{clone_id}/activate", response_model=AssistantConfig)
async def activate_clone(
    clone_id: str,
    request: Request,
    user: UserDep,
    clones: VoiceCloneDep,
    audit: AuditDep,
    tenant_id: str,
) -> AssistantConfig:
    user.require_admin(tenant_id)
    try:
        cfg = await clones.activate(tenant_id, clone_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if cfg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice not found")
    await audit.record(_audit(request, user, tenant_id, "quality.voice_clone.activate", clone_id))
    return cfg


@router.delete("/voice-clones/{clone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_clone(
    clone_id: str,
    request: Request,
    user: UserDep,
    clones: VoiceCloneDep,
    audit: AuditDep,
    tenant_id: str,
) -> None:
    user.require_admin(tenant_id)
    if not await clones.delete(tenant_id, clone_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "voice not found")
    await audit.record(_audit(request, user, tenant_id, "quality.voice_clone.delete", clone_id))
