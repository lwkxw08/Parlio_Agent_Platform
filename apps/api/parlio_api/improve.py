"""Regression pack and auto-improve loop on top of the simulation sandbox (Phase 13b).

Regression pack: any `Scenario` flagged `regression=True` (failed sim scenarios the owner keeps,
or real calls saved as tests) is run against both the live config and a candidate before the
candidate is published. `RegressionCheck.blocked` is set when a scenario that passes today would
fail on the candidate, or the pack's pass count would drop; Studio can still force-publish, which
is recorded on the check.

Auto-improve: `ImproveService.propose()` takes the failing results of a simulation run, drafts a
FAQ or business rule that targets each failure, re-runs the failing scenario plus the regression
pack against that draft, and only keeps `Proposal`s that fix or raise the score without regressing
anything. Nothing is published until the owner approves a proposal (which writes a new assistant
version through the normal path).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from parlio_api.drafting import Drafter, DraftRequest
from parlio_api.qa import Scenario, SimulationResult, SimulationRun, SimulationService
from parlio_api.store import CallStore, TenantDoc
from parlio_voice.models import AssistantConfig, BusinessRule, Faq

log = logging.getLogger("parlio.api.improve")

CHECK_KIND = "regression_check"
PROPOSAL_KIND = "improve_proposal"


# -- regression pack -------------------------------------------------------------------------------


class ScenarioDelta(BaseModel):
    scenario_id: str
    scenario_name: str
    before_passed: bool
    after_passed: bool
    before_score: int
    after_score: int
    after_failures: list[str] = Field(default_factory=list)

    @property
    def regressed(self) -> bool:
        return self.before_passed and not self.after_passed

    @property
    def improved(self) -> bool:
        return (not self.before_passed and self.after_passed) or (
            self.after_passed == self.before_passed and self.after_score > self.before_score
        )


def deltas(before: list[SimulationResult], after: list[SimulationResult]) -> list[ScenarioDelta]:
    by_id = {r.scenario_id: r for r in before}
    out: list[ScenarioDelta] = []
    for a in after:
        b = by_id.get(a.scenario_id)
        if b is None:
            continue
        out.append(
            ScenarioDelta(
                scenario_id=a.scenario_id,
                scenario_name=a.scenario_name,
                before_passed=b.passed,
                after_passed=a.passed,
                before_score=b.score.overall,
                after_score=a.score.overall,
                after_failures=a.failures,
            )
        )
    return out


class RegressionCheck(BaseModel):
    id: str = Field(default_factory=lambda: f"rc-{uuid4().hex[:8]}")
    tenant_id: str
    assistant_id: str
    label: str = "publish"  # publish | proposal:<id>
    pack_size: int = 0
    baseline_passed: int = 0
    candidate_passed: int = 0
    deltas: list[ScenarioDelta] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    blocked: bool = False
    forced: bool = False
    published_version: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=CHECK_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


def _caller_turns(transcript: list[dict[str, object]]) -> list[str]:
    out: list[str] = []
    for t in transcript:
        if t.get("role") in ("user", "caller") and isinstance(t.get("text"), str):
            text = str(t["text"]).strip()
            if text:
                out.append(text[:400])
    return out[:20]


# -- proposals -------------------------------------------------------------------------------------


def apply_fix(
    cfg: AssistantConfig, *, kind: str, title: str, question: str, text: str
) -> AssistantConfig:
    cfg = cfg.model_copy(deep=True)
    if kind == "faq":
        cfg.faqs.append(Faq(question=question, answer=text, source="suggested"))
    else:
        cfg.rules.append(BusinessRule(name=title[:60], instruction=text))
    return cfg


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class Proposal(BaseModel):
    id: str = Field(default_factory=lambda: f"pp-{uuid4().hex[:8]}")
    tenant_id: str
    assistant_id: str
    run_id: str
    kind: str  # faq | rule
    title: str
    question: str
    text: str
    draft_source: str = "template"  # llm | template
    scenario_id: str
    scenario_name: str
    failures_before: list[str] = Field(default_factory=list)
    delta: ScenarioDelta
    check: RegressionCheck
    status: ProposalStatus = ProposalStatus.PROPOSED
    applied_version: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def apply_to(self, cfg: AssistantConfig) -> AssistantConfig:
        return apply_fix(
            cfg, kind=self.kind, title=self.title, question=self.question, text=self.text
        )

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=PROPOSAL_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class _Fix(BaseModel):
    kind: str
    title: str
    question: str
    text: str
    source: str = "template"


class ImproveService:
    def __init__(self, store: CallStore, sim: SimulationService, drafter: Drafter) -> None:
        self.store = store
        self.sim = sim
        self.drafter = drafter

    # -- pack management --

    async def pack(self, tenant_id: str) -> list[Scenario]:
        return [s for s in await self.sim.scenarios(tenant_id) if s.regression]

    async def set_regression(self, tenant_id: str, scenario_id: str, on: bool) -> Scenario | None:
        sc = next((s for s in await self.sim.scenarios(tenant_id) if s.id == scenario_id), None)
        if sc is None:
            return None
        sc.regression = on
        return await self.sim.save_scenario(sc)

    async def add_from_run(self, tenant_id: str, run_id: str) -> list[Scenario]:
        """Keep every scenario that failed in a run as a regression test."""
        run = await self._run(tenant_id, run_id)
        if run is None:
            return []
        failed = {r.scenario_id for r in run.results if r.label == "A" and not r.passed}
        out: list[Scenario] = []
        for sc in await self.sim.scenarios(tenant_id):
            if sc.id in failed and not sc.regression:
                sc.regression = True
                sc.origin = sc.origin or f"run:{run_id}"
                out.append(await self.sim.save_scenario(sc))
        return out

    async def add_from_call(
        self, tenant_id: str, call_id: str, *, name: str | None = None
    ) -> Scenario | None:
        """Turn a real call's caller lines into a scripted regression test."""
        call = await self.store.get_call(call_id)
        if call is None or call.tenant_id != tenant_id:
            return None
        turns = _caller_turns(call.transcript)
        if not turns:
            return None
        sc = Scenario(
            tenant_id=tenant_id,
            name=(name or f"Call {call_id[-6:]}: {turns[0][:50]}")[:80],
            persona="A real caller (replayed from a recorded call)",
            goal=call.summary or "",
            turns=turns,
            regression=True,
            origin=f"call:{call_id}",
        )
        return await self.sim.save_scenario(sc)

    async def checks(self, tenant_id: str, limit: int = 20) -> list[RegressionCheck]:
        docs = await self.store.list_docs(CHECK_KIND, tenant_id, limit)
        return sorted(
            (RegressionCheck.model_validate(d.data) for d in docs),
            key=lambda c: c.created_at,
            reverse=True,
        )

    # -- gate --

    async def check(
        self,
        tenant_id: str,
        candidate: AssistantConfig,
        *,
        label: str = "publish",
        extra: list[Scenario] | None = None,
        persist: bool = True,
    ) -> RegressionCheck:
        if candidate.tenant_id != tenant_id:
            raise ValueError("config belongs to another organisation")
        live = await self.store.get_assistant(candidate.assistant_id)
        if live is None or live.tenant_id != tenant_id:
            raise ValueError("assistant not found")
        pack = await self.pack(tenant_id)
        seen = {s.id for s in pack}
        pack.extend(s for s in (extra or []) if s.id not in seen)
        before: list[SimulationResult] = []
        after: list[SimulationResult] = []
        for sc in pack:
            before.append(await self.sim.run_one(live, sc, label="A", source="live"))
            after.append(await self.sim.run_one(candidate, sc, label="B", source="draft"))
        ds = deltas(before, after)
        regressions = [
            f"{d.scenario_name}: {'; '.join(d.after_failures) or 'now failing'}"
            for d in ds
            if d.regressed
        ]
        bp = sum(d.before_passed for d in ds)
        cp = sum(d.after_passed for d in ds)
        check = RegressionCheck(
            tenant_id=tenant_id,
            assistant_id=candidate.assistant_id,
            label=label,
            pack_size=len(ds),
            baseline_passed=bp,
            candidate_passed=cp,
            deltas=ds,
            regressions=regressions,
            blocked=bool(regressions) or cp < bp,
        )
        if persist:
            await self.store.put_doc(check.to_doc())
        return check

    async def publish(
        self,
        tenant_id: str,
        candidate: AssistantConfig,
        numbers: list[str],
        *,
        force: bool = False,
    ) -> tuple[RegressionCheck, AssistantConfig | None]:
        check = await self.check(tenant_id, candidate, persist=False)
        if check.blocked and not force:
            await self.store.put_doc(check.to_doc())
            return check, None
        await self.store.upsert_assistant(candidate, numbers)
        check.forced = check.blocked and force
        check.published_version = candidate.assistant_version
        await self.store.put_doc(check.to_doc())
        return check, candidate

    # -- auto-improve --

    async def _run(self, tenant_id: str, run_id: str | None) -> SimulationRun | None:
        if run_id is None:
            runs = await self.sim.runs(tenant_id, 1)
            return runs[0] if runs else None
        doc = await self.store.get_doc("sim_run", run_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        return SimulationRun.model_validate(doc.data)

    async def _draft_fix(
        self, cfg: AssistantConfig, sc: Scenario, res: SimulationResult
    ) -> _Fix | None:
        fails = " ".join(res.failures).lower()
        asked = sc.turns[0]
        if "expected a human handoff" in fails:
            return _Fix(
                kind="rule",
                title=f"Transfer when asked: {sc.name}",
                question=asked,
                text=(
                    "When a caller asks to speak to a person (for example: "
                    f"'{asked}'), transfer them to the matching department straight away and "
                    "stay in transfer mode. Only offer a message or callback if that department "
                    "is closed or nobody answers."
                ),
            )
        if "unexpected handoff" in fails:
            return _Fix(
                kind="rule",
                title=f"Answer yourself: {sc.name}",
                question=asked,
                text=(
                    f"Questions like '{asked}' can be answered from the business information; "
                    "answer them yourself and do not hand off unless the caller explicitly asks "
                    "for a person."
                ),
            )
        if "must not contain" in fails or "hallucination" in fails:
            avoid = ", ".join(sc.expect.avoids) or "specific figures"
            return _Fix(
                kind="rule",
                title=f"Do not state: {sc.name}",
                question=asked,
                text=(
                    f"Never state {avoid} for questions like '{asked}'. If the information is "
                    "not in the business knowledge, say you will check and offer to take a "
                    "message so someone can call back."
                ),
            )
        reply = res.turns[0].assistant if res.turns else ""
        brief = (
            f"A caller asked: '{asked}'. The assistant replied: '{reply[:300]}' which was judged "
            f"as: {'; '.join(res.failures)[:400]}. Write the correct, short answer the assistant "
            "should give, using only the business information."
            + (f" It must mention: {', '.join(sc.expect.mentions)}." if sc.expect.mentions else "")
        )
        try:
            d = await self.drafter.draft(
                DraftRequest(field="faq_answer", brief=brief, context=asked), cfg
            )
        except Exception:
            log.warning("draft failed for scenario %s", sc.id, exc_info=True)
            return None
        return _Fix(
            kind="faq",
            title=f"FAQ: {sc.name}",
            question=asked[:200],
            text=d.text.strip(),
            source=d.source,
        )

    async def proposals(
        self, tenant_id: str, status: ProposalStatus | None = None, limit: int = 100
    ) -> list[Proposal]:
        docs = await self.store.list_docs(PROPOSAL_KIND, tenant_id, limit)
        out = [Proposal.model_validate(d.data) for d in docs]
        if status is not None:
            out = [p for p in out if p.status == status]
        return sorted(out, key=lambda p: p.created_at, reverse=True)

    async def propose(
        self, tenant_id: str, assistant_id: str, run_id: str | None = None
    ) -> list[Proposal]:
        run = await self._run(tenant_id, run_id)
        if run is None or run.assistant_id != assistant_id:
            raise ValueError("no simulation run to improve from")
        live = await self.store.get_assistant(assistant_id)
        if live is None or live.tenant_id != tenant_id:
            raise ValueError("assistant not found")
        scen = {s.id: s for s in await self.sim.scenarios(tenant_id)}
        open_ = {
            (p.scenario_id, p.kind)
            for p in await self.proposals(tenant_id, ProposalStatus.PROPOSED)
        }
        out: list[Proposal] = []
        for res in run.results:
            if res.label != "A" or res.passed or res.scenario_id not in scen:
                continue
            sc = scen[res.scenario_id]
            fix = await self._draft_fix(live, sc, res)
            if fix is None or (sc.id, fix.kind) in open_ or not fix.text:
                continue
            candidate = apply_fix(
                live, kind=fix.kind, title=fix.title, question=fix.question, text=fix.text
            )
            proposal_id = f"pp-{uuid4().hex[:8]}"
            check = await self.check(
                tenant_id,
                candidate,
                label=f"proposal:{proposal_id}",
                extra=[sc],
                persist=False,
            )
            target = next((d for d in check.deltas if d.scenario_id == sc.id), None)
            if target is None or check.regressions or not target.improved:
                log.info("dropping candidate fix for %s (no improvement)", sc.name)
                continue
            p = Proposal(
                id=proposal_id,
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                run_id=run.id,
                kind=fix.kind,
                title=fix.title,
                question=fix.question,
                text=fix.text,
                draft_source=fix.source,
                scenario_id=sc.id,
                scenario_name=sc.name,
                failures_before=res.failures,
                delta=target,
                check=check,
            )
            await self.store.put_doc(p.to_doc())
            out.append(p)
        return out

    async def _get(self, tenant_id: str, proposal_id: str) -> Proposal | None:
        doc = await self.store.get_doc(PROPOSAL_KIND, proposal_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        return Proposal.model_validate(doc.data)

    async def approve(
        self, tenant_id: str, proposal_id: str, *, text: str | None = None
    ) -> Proposal | None:
        p = await self._get(tenant_id, proposal_id)
        if p is None or p.status != ProposalStatus.PROPOSED:
            return p
        live = await self.store.get_assistant(p.assistant_id)
        if live is None or live.tenant_id != tenant_id:
            return None
        if text and text.strip():
            p.text = text.strip()
        cfg = p.apply_to(live)
        await self.store.upsert_assistant(cfg, [])
        p.status = ProposalStatus.APPROVED
        p.applied_version = cfg.assistant_version
        p.updated_at = datetime.now(UTC)
        await self.store.put_doc(p.to_doc())
        return p

    async def reject(self, tenant_id: str, proposal_id: str) -> Proposal | None:
        p = await self._get(tenant_id, proposal_id)
        if p is None:
            return None
        p.status = ProposalStatus.REJECTED
        p.updated_at = datetime.now(UTC)
        await self.store.put_doc(p.to_doc())
        return p
