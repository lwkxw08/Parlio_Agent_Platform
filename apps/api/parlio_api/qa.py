"""Phase 13: per-call QA scoring, low-score alerts, insight engine and the simulation sandbox.

`QAScorer.score()` grades a finished call on resolution / tone / accuracy / hallucination risk
(0-10 each) and lists the questions the assistant could not answer. `HeuristicScorer` works
offline from the transcript; `OpenAIScorer` asks the LLM and falls back to the heuristic.

`QAService` runs after post-call analysis, stores a `QAScore` per call (generic tenant doc),
raises `NotifyEvent.QA_LOW_SCORE` when the overall score drops under the tenant threshold, and
clusters unanswered questions into `Insight`s with a suggested FAQ or rule the owner can apply
with one click (writes a new assistant version).

`SimulationService` runs scripted test callers against any `AssistantConfig` — the live one or an
unsaved draft — through the same `TextAgent` the Inbox uses, checks expectations, scores the
result with the QA scorer and supports A/B comparison of two configs on the same scenario.
Browser voice simulation reuses the Phase 11b click-to-talk widget (see routes).

`VoiceCloneService` is a consent-gated seam: a clone can only be requested with an explicit,
recorded owner consent statement; the actual clone is created by a `VoiceCloneProvider`
(simulated unless a vendor is wired in) and the resulting voice id is stored on the assistant.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from parlio_api.inbox import Author, Channel, Direction, InboxMessage, TextAgent, Thread
from parlio_api.notifications import NotificationEvent, NotificationService, NotifyEvent
from parlio_api.store import CallRecord, CallStore, TenantDoc
from parlio_voice.models import AssistantConfig, BusinessRule, Faq, VoiceConfig

log = logging.getLogger("parlio.api.qa")

SCORE_KIND = "qa_score"
SETTINGS_KIND = "qa_settings"
INSIGHT_KIND = "insight"
SCENARIO_KIND = "sim_scenario"
RUN_KIND = "sim_run"
CLONE_KIND = "voice_clone"


# -- models ----------------------------------------------------------------------------------------


class QAFlag(StrEnum):
    UNANSWERED = "unanswered_question"
    HALLUCINATION = "possible_hallucination"
    RUDE = "tone"
    UNRESOLVED = "unresolved"
    ABRUPT_END = "abrupt_end"
    LONG_SILENCE = "slow_turns"
    ESCALATED = "escalated"


class QAScore(BaseModel):
    call_id: str
    tenant_id: str
    assistant_id: str
    resolution: int = Field(ge=0, le=10)
    tone: int = Field(ge=0, le=10)
    accuracy: int = Field(ge=0, le=10)
    hallucination_risk: int = Field(ge=0, le=10, description="0 = none suspected, 10 = certain")
    overall: int = Field(ge=0, le=10)
    flags: list[QAFlag] = Field(default_factory=list)
    unanswered: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    scorer: str = "heuristic"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=SCORE_KIND,
            id=self.call_id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class QASettings(BaseModel):
    tenant_id: str
    enabled: bool = True
    alert_below: int = Field(5, ge=0, le=10, description="Overall score that triggers an alert")
    alert_on_hallucination: bool = True
    min_turns: int = Field(2, ge=0, description="Skip scoring for calls with fewer caller turns")


class QAStats(BaseModel):
    scored: int = 0
    avg_overall: float | None = None
    avg_resolution: float | None = None
    avg_tone: float | None = None
    avg_accuracy: float | None = None
    avg_hallucination_risk: float | None = None
    low_score_calls: int = 0
    flagged_hallucinations: int = 0
    unanswered_questions: int = 0


class InsightStatus(StrEnum):
    OPEN = "open"
    APPLIED = "applied"
    DISMISSED = "dismissed"


class InsightKind(StrEnum):
    FAQ = "faq"
    RULE = "rule"


class Insight(BaseModel):
    id: str = Field(default_factory=lambda: f"in-{uuid4().hex[:8]}")
    tenant_id: str
    assistant_id: str
    kind: InsightKind = InsightKind.FAQ
    question: str
    examples: list[str] = Field(default_factory=list)
    call_ids: list[str] = Field(default_factory=list)
    count: int = 1
    suggested_answer: str | None = None
    suggested_rule: str | None = None
    status: InsightStatus = InsightStatus.OPEN
    applied_version: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=INSIGHT_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


# -- scorers ---------------------------------------------------------------------------------------


class QAScorer(Protocol):
    name: str

    async def score(self, call: CallRecord, cfg: AssistantConfig | None) -> QAScore: ...


_UNSURE = (
    "i don't know",
    "i'm not sure",
    "i am not sure",
    "i don't have that information",
    "i can't help with",
    "i'm unable to",
    "i am unable to",
    "not something i can",
    "i'll pass that on",
    "i'll pass your question",
    "someone will get back to you",
    "i don't have details",
)
_RUDE = ("stupid", "shut up", "idiot", "whatever", "not my problem", "calm down")
_UNHAPPY = ("useless", "ridiculous", "complaint", "angry", "frustrat", "terrible", "awful")
_THANKS = ("thank", "great", "perfect", "brilliant", "lovely", "cheers", "that's all")
_RESOLVED = (
    "booked",
    "confirmed",
    "arranged",
    "sent you",
    "transferr",
    "i've logged",
    "i have logged",
)
_NUM = re.compile(
    r"(£\s?\d[\d,.]*|\b\d{1,2}(:\d{2})?\s?(am|pm)\b|\b\d+\s?(%|percent|minutes|days|weeks))", re.I
)
_WORD = re.compile(r"[a-z0-9']+")
_STOP = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "do",
    "does",
    "you",
    "i",
    "to",
    "of",
    "and",
    "what",
    "how",
    "can",
    "we",
    "it",
    "in",
    "on",
    "for",
    "my",
    "me",
    "please",
    "hi",
    "hello",
    "there",
    "your",
    "have",
    "be",
    "with",
    "that",
    "this",
    "at",
    "or",
    "if",
    "so",
    "would",
    "like",
    "could",
}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1}


def _turns(call: CallRecord, role: str) -> list[str]:
    return [str(t.get("text") or "") for t in call.transcript if t.get("role") == role]


def _clamp(v: float) -> int:
    return max(0, min(10, round(v)))


def _knowledge_text(cfg: AssistantConfig | None) -> str:
    if cfg is None:
        return ""
    parts = [cfg.instructions, cfg.greeting, cfg.business.model_dump_json()]
    parts += [f"{f.question} {f.answer}" for f in cfg.faqs]
    parts += [r.instruction for r in cfg.rules]
    return " ".join(parts).lower()


def unanswered_questions(call: CallRecord) -> list[str]:
    """Caller questions immediately followed by an assistant 'I don't know / will pass on'."""
    out: list[str] = []
    items = call.transcript
    for i, t in enumerate(items):
        if t.get("role") != "assistant":
            continue
        low = str(t.get("text") or "").lower()
        if not any(k in low for k in _UNSURE):
            continue
        prev = next(
            (str(p.get("text") or "") for p in reversed(items[:i]) if p.get("role") == "user"),
            None,
        )
        if prev and len(prev.split()) >= 3 and prev not in out:
            out.append(prev.strip()[:200])
    return out


class HeuristicScorer:
    """Offline grader driven by transcript patterns; conservative about hallucination."""

    name = "heuristic"

    async def score(self, call: CallRecord, cfg: AssistantConfig | None) -> QAScore:
        user, bot = _turns(call, "user"), _turns(call, "assistant")
        flags: list[QAFlag] = []
        notes: list[str] = []
        unanswered = unanswered_questions(call)
        if unanswered:
            flags.append(QAFlag.UNANSWERED)

        bot_text = " ".join(bot).lower()
        user_text = " ".join(user).lower()

        # resolution: did the call end with something achieved / caller satisfied?
        resolution = 6.0
        if call.ticket_ids or call.transfers or any(k in bot_text for k in _RESOLVED):
            resolution += 2
        if any(k in user_text[-300:] for k in _THANKS):
            resolution += 1.5
        if unanswered:
            resolution -= 1.5 * min(len(unanswered), 3)
        if call.missed_fields:
            resolution -= 1
        if call.end_reason in ("error", "failed", "timeout") or (
            call.answered_at is not None and (call.duration_s or 0) < 8 and len(user) <= 1
        ):
            resolution -= 3
            flags.append(QAFlag.ABRUPT_END)
        if call.escalated:
            flags.append(QAFlag.ESCALATED)
            notes.append(f"escalated on '{call.escalation_keyword}'")
        if resolution < 5:
            flags.append(QAFlag.UNRESOLVED)

        # tone: assistant rudeness or caller frustration signals
        tone = 8.0
        if any(k in bot_text for k in _RUDE):
            tone -= 5
            flags.append(QAFlag.RUDE)
        if any(k in user_text for k in _UNHAPPY):
            tone -= 2
            notes.append("caller expressed frustration")
        interrupted = sum(1 for t in call.transcript if t.get("interrupted"))
        if interrupted >= 3:
            tone -= 1
            notes.append(f"assistant interrupted {interrupted}x")
        if len(bot) >= 3 and all(len(b.split()) > 60 for b in bot[:3]):
            tone -= 1
            notes.append("long-winded replies")

        # accuracy / hallucination: concrete claims (prices, times, durations) not in knowledge
        knowledge = _knowledge_text(cfg)
        claims = [m.group(0) for b in bot for m in _NUM.finditer(b)]
        unsupported = [
            c
            for c in claims
            if knowledge and c.lower().replace(" ", "") not in knowledge.replace(" ", "")
        ]
        hallucination = 1.0
        accuracy = 8.0
        if unsupported:
            hallucination += 2.5 * min(len(unsupported), 3)
            accuracy -= 1.5 * min(len(unsupported), 3)
            flags.append(QAFlag.HALLUCINATION)
            notes.append("unverified specifics: " + ", ".join(unsupported[:4]))
        if unanswered:
            accuracy -= 0.5 * len(unanswered)

        p95 = call.latency.get("p95_s")
        if isinstance(p95, int | float) and p95 > 2.5:
            flags.append(QAFlag.LONG_SILENCE)
            notes.append(f"slow turns (p95 {p95:.1f}s)")

        r, t, a, h = _clamp(resolution), _clamp(tone), _clamp(accuracy), _clamp(hallucination)
        overall = _clamp(0.4 * r + 0.25 * t + 0.25 * a + 0.1 * (10 - h))
        return QAScore(
            call_id=call.call_id,
            tenant_id=call.tenant_id,
            assistant_id=call.assistant_id,
            resolution=r,
            tone=t,
            accuracy=a,
            hallucination_risk=h,
            overall=overall,
            flags=list(dict.fromkeys(flags)),
            unanswered=unanswered,
            notes=notes,
            scorer=self.name,
        )


class _LLMScore(BaseModel):
    resolution: int = Field(ge=0, le=10)
    tone: int = Field(ge=0, le=10)
    accuracy: int = Field(ge=0, le=10)
    hallucination_risk: int = Field(ge=0, le=10)
    unanswered: list[str] = Field(default_factory=list)
    hallucinations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class OpenAIScorer:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
        fallback: QAScorer | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
        )
        self._model = model
        self._fallback = fallback or HeuristicScorer()

    async def score(self, call: CallRecord, cfg: AssistantConfig | None) -> QAScore:
        transcript = "\n".join(f"{t.get('role')}: {t.get('text')}" for t in call.transcript)
        knowledge = _knowledge_text(cfg)[:6000]
        prompt = (
            "You are a QA reviewer for an AI phone receptionist serving a UK business. Grade the "
            "call 0-10 on: resolution (did the caller get what they needed), tone (polite, "
            "concise, natural), accuracy (answers consistent with the business knowledge below), "
            "and hallucination_risk (10 = the assistant confidently stated facts not in the "
            "knowledge). List caller questions the assistant could not answer, and any "
            "statements that look invented. Return JSON with keys resolution, tone, accuracy, "
            "hallucination_risk, unanswered (list), hallucinations (list), notes (list).\n\n"
            f"Business knowledge:\n{knowledge}\n\nTranscript:\n{transcript}"
        )
        try:
            r = await self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            r.raise_for_status()
            data = _LLMScore.model_validate_json(r.json()["choices"][0]["message"]["content"])
        except Exception:
            log.warning("LLM QA failed for %s; using heuristic", call.call_id, exc_info=True)
            return await self._fallback.score(call, cfg)
        base = await self._fallback.score(call, cfg)
        flags: list[QAFlag] = [
            f for f in base.flags if f in (QAFlag.ESCALATED, QAFlag.LONG_SILENCE)
        ]
        if data.unanswered:
            flags.append(QAFlag.UNANSWERED)
        if data.hallucinations or data.hallucination_risk >= 6:
            flags.append(QAFlag.HALLUCINATION)
        if data.resolution < 5:
            flags.append(QAFlag.UNRESOLVED)
        if data.tone < 5:
            flags.append(QAFlag.RUDE)
        overall = _clamp(
            0.4 * data.resolution
            + 0.25 * data.tone
            + 0.25 * data.accuracy
            + 0.1 * (10 - data.hallucination_risk)
        )
        return QAScore(
            call_id=call.call_id,
            tenant_id=call.tenant_id,
            assistant_id=call.assistant_id,
            resolution=data.resolution,
            tone=data.tone,
            accuracy=data.accuracy,
            hallucination_risk=data.hallucination_risk,
            overall=overall,
            flags=list(dict.fromkeys(flags)),
            unanswered=[u[:200] for u in data.unanswered][:10],
            notes=(data.notes + [f"possible hallucination: {h}" for h in data.hallucinations])[:10],
            scorer=self.name,
        )


# -- insight clustering ----------------------------------------------------------------------------


def _similar(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    inter = len(a & b)
    return inter / len(a | b) >= 0.4 or inter >= 3


def cluster_questions(items: Iterable[tuple[str, str]]) -> list[tuple[str, list[str], list[str]]]:
    """Group (question, call_id) pairs by token overlap -> (representative, examples, call_ids)."""
    clusters: list[tuple[set[str], list[str], list[str]]] = []
    for q, call_id in items:
        toks = _tokens(q)
        for ctoks, examples, ids in clusters:
            if _similar(ctoks, toks):
                ctoks |= toks
                if q not in examples:
                    examples.append(q)
                if call_id not in ids:
                    ids.append(call_id)
                break
        else:
            clusters.append((set(toks), [q], [call_id]))
    out = [(min(ex, key=len), ex, ids) for _, ex, ids in clusters]
    out.sort(key=lambda c: -len(c[2]))
    return out


def _is_rule_question(q: str) -> bool:
    low = q.lower()
    return any(k in low for k in ("can you", "could you", "will you", "do you do", "are you able"))


# -- service ---------------------------------------------------------------------------------------


class QAService:
    def __init__(
        self,
        store: CallStore,
        scorer: QAScorer,
        notifications: NotificationService | None = None,
        dashboard_url: str = "",
    ) -> None:
        self.store = store
        self.scorer = scorer
        self.notifications = notifications
        self.dashboard_url = dashboard_url.rstrip("/")

    # settings
    async def settings(self, tenant_id: str) -> QASettings:
        doc = await self.store.get_doc(SETTINGS_KIND, tenant_id)
        return QASettings.model_validate(doc.data) if doc else QASettings(tenant_id=tenant_id)

    async def save_settings(self, s: QASettings) -> QASettings:
        await self.store.put_doc(
            TenantDoc(
                kind=SETTINGS_KIND, id=s.tenant_id, tenant_id=s.tenant_id, data=s.model_dump()
            )
        )
        return s

    # scoring
    async def score_call(self, call: CallRecord, *, force: bool = False) -> QAScore | None:
        s = await self.settings(call.tenant_id)
        if not s.enabled and not force:
            return None
        if call.kind == "blocked" or call.answered_at is None:
            return None
        if len(_turns(call, "user")) < s.min_turns and not force:
            return None
        if not force:
            existing = await self.get_score(call.tenant_id, call.call_id)
            if existing is not None:
                return existing
        cfg = await self.store.get_assistant(call.assistant_id)
        score = await self.scorer.score(call, cfg)
        await self.store.put_doc(score.to_doc())
        await self._maybe_alert(score, s, cfg)
        await self._update_insights(score, cfg)
        return score

    async def _maybe_alert(
        self, score: QAScore, s: QASettings, cfg: AssistantConfig | None
    ) -> None:
        if self.notifications is None:
            return
        low = score.overall < s.alert_below
        halluc = s.alert_on_hallucination and QAFlag.HALLUCINATION in score.flags
        if not (low or halluc):
            return
        why = "low QA score" if low else "possible hallucination"
        link = f"{self.dashboard_url}/quality?call={score.call_id}" if self.dashboard_url else ""
        await self.notifications.dispatch(
            NotificationEvent(
                tenant_id=score.tenant_id,
                company_id=cfg.company_id if cfg else None,
                event=NotifyEvent.QA_LOW_SCORE,
                title=f"Call quality alert ({why}): {score.overall}/10",
                body=(
                    f"resolution {score.resolution}, tone {score.tone}, accuracy {score.accuracy}, "
                    f"hallucination risk {score.hallucination_risk}. "
                    + ("; ".join(score.notes[:3]) if score.notes else "")
                    + (f" {link}" if link else "")
                ).strip(),
                context={"call_id": score.call_id, "score": score.model_dump(mode="json")},
            )
        )

    async def get_score(self, tenant_id: str, call_id: str) -> QAScore | None:
        doc = await self.store.get_doc(SCORE_KIND, call_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        return QAScore.model_validate(doc.data)

    async def list_scores(self, tenant_id: str, limit: int = 200) -> list[QAScore]:
        docs = await self.store.list_docs(SCORE_KIND, tenant_id, limit)
        out = [QAScore.model_validate(d.data) for d in docs]
        out.sort(key=lambda s: s.created_at, reverse=True)
        return out

    async def stats(self, tenant_id: str) -> QAStats:
        scores = await self.list_scores(tenant_id, 1000)
        s = await self.settings(tenant_id)
        if not scores:
            return QAStats()
        n = len(scores)

        def avg(vals: list[int]) -> float:
            return round(sum(vals) / n, 2)

        return QAStats(
            scored=n,
            avg_overall=avg([x.overall for x in scores]),
            avg_resolution=avg([x.resolution for x in scores]),
            avg_tone=avg([x.tone for x in scores]),
            avg_accuracy=avg([x.accuracy for x in scores]),
            avg_hallucination_risk=avg([x.hallucination_risk for x in scores]),
            low_score_calls=sum(1 for x in scores if x.overall < s.alert_below),
            flagged_hallucinations=sum(1 for x in scores if QAFlag.HALLUCINATION in x.flags),
            unanswered_questions=sum(len(x.unanswered) for x in scores),
        )

    # insights
    async def list_insights(
        self, tenant_id: str, status: InsightStatus | None = None
    ) -> list[Insight]:
        docs = await self.store.list_docs(INSIGHT_KIND, tenant_id, 500)
        out = [Insight.model_validate(d.data) for d in docs]
        if status is not None:
            out = [i for i in out if i.status == status]
        out.sort(key=lambda i: (-i.count, i.created_at))
        return out

    async def _update_insights(self, score: QAScore, cfg: AssistantConfig | None) -> None:
        if not score.unanswered:
            return
        existing = await self.list_insights(score.tenant_id)
        open_ = [i for i in existing if i.status == InsightStatus.OPEN]
        for q in score.unanswered:
            toks = _tokens(q)
            hit = next(
                (
                    i
                    for i in open_
                    if i.assistant_id == score.assistant_id
                    and _similar(_tokens(" ".join([i.question, *i.examples])), toks)
                ),
                None,
            )
            if hit is not None:
                if score.call_id not in hit.call_ids:
                    hit.call_ids.append(score.call_id)
                    hit.count = len(hit.call_ids)
                if q not in hit.examples and len(hit.examples) < 5:
                    hit.examples.append(q)
                hit.updated_at = datetime.now(UTC)
                await self.store.put_doc(hit.to_doc())
                continue
            kind = InsightKind.RULE if _is_rule_question(q) else InsightKind.FAQ
            ins = Insight(
                tenant_id=score.tenant_id,
                assistant_id=score.assistant_id,
                kind=kind,
                question=q,
                examples=[q],
                call_ids=[score.call_id],
                suggested_answer=None,
                suggested_rule=(
                    f"When callers ask '{q}', explain clearly whether "
                    f"{cfg.business_name if cfg else 'the business'} offers this "
                    "and what to do next."
                    if kind == InsightKind.RULE
                    else None
                ),
            )
            open_.append(ins)
            await self.store.put_doc(ins.to_doc())

    async def rebuild_insights(self, tenant_id: str) -> list[Insight]:
        """Re-cluster every open unanswered question from stored scores (idempotent)."""
        scores = await self.list_scores(tenant_id, 1000)
        existing = await self.list_insights(tenant_id)
        keep = [i for i in existing if i.status != InsightStatus.OPEN]
        for i in existing:
            if i.status == InsightStatus.OPEN:
                await self.store.delete_doc(INSIGHT_KIND, i.id)
        by_assistant: dict[str, list[tuple[str, str]]] = {}
        for s in scores:
            for q in s.unanswered:
                by_assistant.setdefault(s.assistant_id, []).append((q, s.call_id))
        out: list[Insight] = list(keep)
        for assistant_id, items in by_assistant.items():
            for rep, examples, ids in cluster_questions(items):
                kind = InsightKind.RULE if _is_rule_question(rep) else InsightKind.FAQ
                ins = Insight(
                    tenant_id=tenant_id,
                    assistant_id=assistant_id,
                    kind=kind,
                    question=rep,
                    examples=examples[:5],
                    call_ids=ids,
                    count=len(ids),
                )
                await self.store.put_doc(ins.to_doc())
                out.append(ins)
        out.sort(key=lambda i: (-i.count, i.created_at))
        return out

    async def _get_insight(self, tenant_id: str, insight_id: str) -> Insight | None:
        doc = await self.store.get_doc(INSIGHT_KIND, insight_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        return Insight.model_validate(doc.data)

    async def apply_insight(
        self,
        tenant_id: str,
        insight_id: str,
        *,
        answer: str | None = None,
        rule: str | None = None,
        question: str | None = None,
    ) -> Insight | None:
        ins = await self._get_insight(tenant_id, insight_id)
        if ins is None:
            return None
        cfg = await self.store.get_assistant(ins.assistant_id)
        if cfg is None or cfg.tenant_id != tenant_id:
            return None
        if ins.kind == InsightKind.FAQ:
            text = (answer or ins.suggested_answer or "").strip()
            if not text:
                raise ValueError("an answer is required to add the FAQ")
            cfg.faqs.append(
                Faq(question=(question or ins.question).strip(), answer=text, source="insight")
            )
        else:
            text = (rule or ins.suggested_rule or "").strip()
            if not text:
                raise ValueError("an instruction is required to add the rule")
            cfg.rules.append(BusinessRule(name=(question or ins.question)[:60], instruction=text))
        await self.store.upsert_assistant(cfg, [])
        ins.status = InsightStatus.APPLIED
        ins.applied_version = cfg.assistant_version
        ins.suggested_answer = answer or ins.suggested_answer
        ins.suggested_rule = rule or ins.suggested_rule
        ins.updated_at = datetime.now(UTC)
        await self.store.put_doc(ins.to_doc())
        return ins

    async def dismiss_insight(self, tenant_id: str, insight_id: str) -> Insight | None:
        ins = await self._get_insight(tenant_id, insight_id)
        if ins is None:
            return None
        ins.status = InsightStatus.DISMISSED
        ins.updated_at = datetime.now(UTC)
        await self.store.put_doc(ins.to_doc())
        return ins


# -- simulation sandbox ----------------------------------------------------------------------------


class Expectation(BaseModel):
    mentions: list[str] = Field(default_factory=list, description="phrases expected in replies")
    avoids: list[str] = Field(default_factory=list, description="phrases that must not appear")
    handoff: bool | None = Field(None, description="expect (or forbid) a human handoff")
    ticket: bool | None = Field(None, description="expect (or forbid) a ticket/message taken")
    min_overall: int = Field(6, ge=0, le=10)


class Scenario(BaseModel):
    id: str = Field(default_factory=lambda: f"sc-{uuid4().hex[:8]}")
    tenant_id: str
    name: str = Field(min_length=1, max_length=80)
    persona: str = "A polite first-time caller"
    goal: str = ""
    turns: list[str] = Field(min_length=1, max_length=20, description="scripted caller lines")
    expect: Expectation = Field(default_factory=Expectation)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=SCENARIO_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


DEFAULT_SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "Opening hours",
        "persona": "Busy local customer",
        "goal": "Find out when the business is open",
        "turns": ["Hi, what time are you open until today?", "Great, thanks."],
        "expect": {"mentions": ["open"], "handoff": False},
    },
    {
        "name": "Book an appointment",
        "persona": "New customer who wants to book",
        "goal": "Book a slot",
        "turns": [
            "Hello, I'd like to book an appointment please.",
            "Thursday afternoon if possible.",
        ],
        "expect": {"mentions": ["book"]},
    },
    {
        "name": "Wants a human",
        "persona": "Frustrated existing customer",
        "goal": "Speak to a person",
        "turns": ["I need to speak to a real person right now.", "It's about an invoice."],
        "expect": {"handoff": True},
    },
    {
        "name": "Off-topic / unknown",
        "persona": "Caller asking something the business does not cover",
        "goal": "Check the assistant admits it does not know rather than inventing",
        "turns": ["Do you sell second-hand tractors and what's the price?"],
        "expect": {"avoids": ["£"], "min_overall": 4},
    },
]


class SimTurn(BaseModel):
    caller: str
    assistant: str
    handoff: bool = False
    ticket: bool = False


class SimulationResult(BaseModel):
    scenario_id: str
    scenario_name: str
    label: str = "A"
    config_source: str = "live"  # live | draft | version:N
    turns: list[SimTurn]
    score: QAScore
    passed: bool
    failures: list[str] = Field(default_factory=list)
    agent: str = "rules"


class SimulationRun(BaseModel):
    id: str = Field(default_factory=lambda: f"sr-{uuid4().hex[:8]}")
    tenant_id: str
    assistant_id: str
    results: list[SimulationResult]
    winner: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=RUN_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class SimulationService:
    def __init__(self, store: CallStore, agent: TextAgent, scorer: QAScorer) -> None:
        self.store = store
        self.agent = agent
        self.scorer = scorer

    async def scenarios(self, tenant_id: str) -> list[Scenario]:
        docs = await self.store.list_docs(SCENARIO_KIND, tenant_id, 200)
        if not docs:
            out = [Scenario(tenant_id=tenant_id, **d) for d in DEFAULT_SCENARIOS]
            for s in out:
                await self.store.put_doc(s.to_doc())
            return out
        return sorted((Scenario.model_validate(d.data) for d in docs), key=lambda s: s.created_at)

    async def save_scenario(self, s: Scenario) -> Scenario:
        await self.store.put_doc(s.to_doc())
        return s

    async def delete_scenario(self, tenant_id: str, scenario_id: str) -> bool:
        doc = await self.store.get_doc(SCENARIO_KIND, scenario_id)
        if doc is None or doc.tenant_id != tenant_id:
            return False
        return await self.store.delete_doc(SCENARIO_KIND, scenario_id)

    async def run_one(
        self, cfg: AssistantConfig, scenario: Scenario, *, label: str, source: str
    ) -> SimulationResult:
        thread = Thread(
            tenant_id=cfg.tenant_id,
            company_id=cfg.company_id,
            channel=Channel.WEBCHAT,
            identity=f"sim:{scenario.id}",
            contact_name="Test caller",
        )
        history: list[InboxMessage] = []
        turns: list[SimTurn] = []
        transcript: list[dict[str, Any]] = [{"role": "assistant", "text": cfg.greeting}]
        handoff = ticket = False
        for line in scenario.turns:
            history.append(
                InboxMessage(
                    tenant_id=cfg.tenant_id,
                    thread_id=thread.id,
                    channel=Channel.WEBCHAT,
                    direction=Direction.IN,
                    author=Author.CONTACT,
                    text=line,
                    status="received",
                )
            )
            transcript.append({"role": "user", "text": line})
            turn = await self.agent.respond(cfg, thread, history)
            history.append(
                InboxMessage(
                    tenant_id=cfg.tenant_id,
                    thread_id=thread.id,
                    channel=Channel.WEBCHAT,
                    direction=Direction.OUT,
                    author=Author.AI,
                    text=turn.reply,
                )
            )
            transcript.append({"role": "assistant", "text": turn.reply})
            handoff = handoff or turn.handoff
            ticket = ticket or turn.ticket is not None
            turns.append(
                SimTurn(
                    caller=line,
                    assistant=turn.reply,
                    handoff=turn.handoff,
                    ticket=turn.ticket is not None,
                )
            )
        now = datetime.now(UTC)
        fake = CallRecord(
            call_id=f"sim-{uuid4().hex[:8]}",
            tenant_id=cfg.tenant_id,
            company_id=cfg.company_id,
            assistant_id=cfg.assistant_id,
            caller="sim",
            status="completed",
            answered_at=now,
            ended_at=now,
            duration_s=float(30 * len(scenario.turns)),
            transcript=transcript,
            ticket_ids=["sim"] if ticket else [],
        )
        score = await self.scorer.score(fake, cfg)
        failures: list[str] = []
        replies = " ".join(t.assistant for t in turns).lower()
        for m in scenario.expect.mentions:
            if m.lower() not in replies:
                failures.append(f"expected reply to mention '{m}'")
        for a in scenario.expect.avoids:
            if a.lower() in replies:
                failures.append(f"reply must not contain '{a}'")
        if scenario.expect.handoff is not None and scenario.expect.handoff != handoff:
            failures.append(
                "expected a human handoff" if scenario.expect.handoff else "unexpected handoff"
            )
        if scenario.expect.ticket is not None and scenario.expect.ticket != ticket:
            failures.append(
                "expected a ticket/message" if scenario.expect.ticket else "unexpected ticket"
            )
        if score.overall < scenario.expect.min_overall:
            failures.append(f"QA score {score.overall} below {scenario.expect.min_overall}")
        return SimulationResult(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            label=label,
            config_source=source,
            turns=turns,
            score=score,
            passed=not failures,
            failures=failures,
            agent=self.agent.name,
        )

    async def run(
        self,
        tenant_id: str,
        assistant_id: str,
        scenario_ids: list[str],
        *,
        draft: AssistantConfig | None = None,
        variant_b: AssistantConfig | None = None,
    ) -> SimulationRun:
        live = await self.store.get_assistant(assistant_id)
        if live is None or live.tenant_id != tenant_id:
            raise ValueError("assistant not found")
        cfg_a = draft or live
        if cfg_a.tenant_id != tenant_id or (variant_b and variant_b.tenant_id != tenant_id):
            raise ValueError("config belongs to another organisation")
        all_scen = {s.id: s for s in await self.scenarios(tenant_id)}
        chosen = [all_scen[i] for i in scenario_ids if i in all_scen] or list(all_scen.values())
        results: list[SimulationResult] = []
        for sc in chosen:
            results.append(
                await self.run_one(cfg_a, sc, label="A", source="draft" if draft else "live")
            )
            if variant_b is not None:
                results.append(await self.run_one(variant_b, sc, label="B", source="draft"))
        winner: str | None = None
        if variant_b is not None:
            a = [r for r in results if r.label == "A"]
            b = [r for r in results if r.label == "B"]
            sa = (sum(r.passed for r in a), sum(r.score.overall for r in a))
            sb = (sum(r.passed for r in b), sum(r.score.overall for r in b))
            winner = "A" if sa > sb else "B" if sb > sa else "tie"
        run = SimulationRun(
            tenant_id=tenant_id, assistant_id=assistant_id, results=results, winner=winner
        )
        await self.store.put_doc(run.to_doc())
        return run

    async def runs(self, tenant_id: str, limit: int = 20) -> list[SimulationRun]:
        docs = await self.store.list_docs(RUN_KIND, tenant_id, limit)
        return sorted(
            (SimulationRun.model_validate(d.data) for d in docs),
            key=lambda r: r.created_at,
            reverse=True,
        )


# -- voice cloning (consent-gated seam) ------------------------------------------------------------

CONSENT_STATEMENT = (
    "I confirm I am the person whose voice is in this recording (or have their written "
    "permission), and I consent to Parlio creating a synthetic voice from it for this "
    "organisation's assistant. I can withdraw consent and delete the voice at any time."
)


class VoiceCloneStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class VoiceClone(BaseModel):
    id: str = Field(default_factory=lambda: f"vc-{uuid4().hex[:8]}")
    tenant_id: str
    assistant_id: str
    name: str
    consent_by: str  # user id/email that ticked the box
    consent_statement: str
    consent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sample_seconds: float
    provider: str
    provider_voice_id: str | None = None
    status: VoiceCloneStatus = VoiceCloneStatus.PENDING
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=CLONE_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class VoiceCloneProvider(Protocol):
    name: str

    async def create(self, name: str, sample: bytes) -> str: ...
    async def delete(self, voice_id: str) -> None: ...


class SimulatedCloneProvider:
    """No vendor configured: records the request and returns a placeholder id (not audible)."""

    name = "simulated"

    async def create(self, name: str, sample: bytes) -> str:
        return f"sim-voice-{uuid4().hex[:8]}"

    async def delete(self, voice_id: str) -> None:
        return None


class VoiceCloneService:
    MIN_SAMPLE_S = 10.0

    def __init__(self, store: CallStore, provider: VoiceCloneProvider) -> None:
        self.store = store
        self.provider = provider

    async def list(self, tenant_id: str) -> list[VoiceClone]:
        docs = await self.store.list_docs(CLONE_KIND, tenant_id, 50)
        return sorted((VoiceClone.model_validate(d.data) for d in docs), key=lambda c: c.created_at)

    async def request(
        self,
        cfg: AssistantConfig,
        *,
        name: str,
        consent: bool,
        consent_by: str,
        sample: bytes,
        sample_seconds: float,
    ) -> VoiceClone:
        if not consent:
            raise ValueError("explicit consent is required to clone a voice")
        if sample_seconds < self.MIN_SAMPLE_S or not sample:
            raise ValueError(f"provide at least {int(self.MIN_SAMPLE_S)}s of clear speech")
        clone = VoiceClone(
            tenant_id=cfg.tenant_id,
            assistant_id=cfg.assistant_id,
            name=name,
            consent_by=consent_by,
            consent_statement=CONSENT_STATEMENT,
            sample_seconds=sample_seconds,
            provider=self.provider.name,
        )
        try:
            clone.provider_voice_id = await self.provider.create(name, sample)
            clone.status = VoiceCloneStatus.READY
        except Exception as e:
            clone.status = VoiceCloneStatus.FAILED
            clone.error = str(e)[:200]
        await self.store.put_doc(clone.to_doc())
        return clone

    async def activate(self, tenant_id: str, clone_id: str) -> AssistantConfig | None:
        doc = await self.store.get_doc(CLONE_KIND, clone_id)
        if doc is None or doc.tenant_id != tenant_id:
            return None
        clone = VoiceClone.model_validate(doc.data)
        if clone.status != VoiceCloneStatus.READY or not clone.provider_voice_id:
            raise ValueError("voice is not ready")
        cfg = await self.store.get_assistant(clone.assistant_id)
        if cfg is None or cfg.tenant_id != tenant_id:
            return None
        cfg.voice.voice_id = clone.provider_voice_id
        await self.store.upsert_assistant(cfg, [])
        return cfg

    async def delete(self, tenant_id: str, clone_id: str) -> bool:
        doc = await self.store.get_doc(CLONE_KIND, clone_id)
        if doc is None or doc.tenant_id != tenant_id:
            return False
        clone = VoiceClone.model_validate(doc.data)
        if clone.provider_voice_id:
            try:
                await self.provider.delete(clone.provider_voice_id)
            except Exception:
                log.warning("voice clone delete failed for %s", clone_id, exc_info=True)
        cfg = await self.store.get_assistant(clone.assistant_id)
        if cfg is not None and cfg.voice.voice_id == clone.provider_voice_id:
            cfg.voice.voice_id = VoiceConfig().voice_id
            await self.store.upsert_assistant(cfg, [])
        clone.status = VoiceCloneStatus.DELETED
        clone.provider_voice_id = None
        await self.store.put_doc(clone.to_doc())
        return True
