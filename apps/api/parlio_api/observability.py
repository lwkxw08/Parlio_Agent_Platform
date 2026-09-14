"""Phase 6 hardening: per-call OpenTelemetry traces, Prometheus metrics, per-tenant rate limits,
and an append-only audit log.

Traces: one span per call (`parlio.call`) opened on call.started and closed on call.ended /
call.failed, carrying answer latency and the worker's turn-latency summary as attributes. They are
exported over OTLP when `PARLIO_OTLP_ENDPOINT` is set, otherwise kept in-process (no-op exporter)
so the code path is identical in dev and prod.
"""

from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, StatusCode
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from parlio_api.store import CallFilter, CallStore, TenantDoc
from parlio_voice.models import CallEvent, CallEventType

log = logging.getLogger("parlio.observability")


def build_tracer(service: str, env: str, otlp_endpoint: str | None) -> trace.Tracer:
    provider = TracerProvider(
        resource=Resource.create({"service.name": service, "deployment.environment": env})
    )
    if otlp_endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    return provider.get_tracer("parlio")


class LatencyBucket(BaseModel):
    calls: int = 0
    answered: int = 0
    answer_p50_s: float | None = None
    answer_p95_s: float | None = None
    turn_p50_s: float | None = None
    turn_p95_s: float | None = None
    eou_avg_s: float | None = None
    llm_ttft_avg_s: float | None = None
    tts_ttfb_avg_s: float | None = None
    slow_calls: int = 0  # turn p95 > target


class LatencyReport(BaseModel):
    tenant_id: str | None
    since: datetime
    until: datetime
    target_turn_s: float
    overall: LatencyBucket
    per_day: dict[str, LatencyBucket]
    per_assistant: dict[str, LatencyBucket]


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    return round(vals[max(0, round(q * (len(vals) - 1)))], 3)


def _avg(vals: list[float]) -> float | None:
    return round(statistics.fmean(vals), 3) if vals else None


class Telemetry:
    """Call spans + Prometheus metrics; safe to call from the event consumer hot path."""

    def __init__(self, tracer: trace.Tracer, target_turn_s: float = 1.5) -> None:
        self.tracer = tracer
        self.target_turn_s = target_turn_s
        self._spans: dict[str, Span] = {}
        self.registry = CollectorRegistry()
        self.calls_total = Counter(
            "parlio_calls_total", "Calls by outcome", ["tenant", "outcome"], registry=self.registry
        )
        self.answer_latency = Histogram(
            "parlio_answer_latency_seconds",
            "Ring-to-greeting latency",
            buckets=(0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5),
            registry=self.registry,
        )
        self.turn_p95 = Histogram(
            "parlio_turn_p95_seconds",
            "Per-call p95 turn latency (EOU -> first TTS byte)",
            buckets=(0.5, 0.75, 1, 1.25, 1.5, 2, 3, 5),
            registry=self.registry,
        )
        self.active_calls = Gauge(
            "parlio_active_calls", "Calls in progress", ["tenant"], registry=self.registry
        )
        self.events_total = Counter(
            "parlio_events_total", "Call events ingested", ["type"], registry=self.registry
        )
        self.rate_limited = Counter(
            "parlio_rate_limited_total",
            "Requests rejected by tenant rate limit",
            ["tenant"],
            registry=self.registry,
        )

    def on_event(self, ev: CallEvent) -> None:
        self.events_total.labels(ev.type.value).inc()
        t = ev.tenant_id
        if ev.type == CallEventType.CALL_STARTED:
            span = self.tracer.start_span(
                "parlio.call",
                attributes={
                    "parlio.tenant_id": t,
                    "parlio.assistant_id": ev.assistant_id,
                    "parlio.call_id": ev.call_id,
                    "parlio.dialed": str(ev.payload.get("dialed", "")),
                },
            )
            self._spans[ev.call_id] = span
            self.active_calls.labels(t).inc()
        elif ev.type == CallEventType.CALL_ANSWERED:
            lat = ev.payload.get("answer_latency_s")
            if isinstance(lat, int | float):
                self.answer_latency.observe(float(lat))
                if (open_span := self._spans.get(ev.call_id)) is not None:
                    open_span.set_attribute("parlio.answer_latency_s", float(lat))
                    open_span.add_event("answered")
        elif ev.type in (CallEventType.CALL_ENDED, CallEventType.CALL_FAILED):
            outcome = "failed" if ev.type == CallEventType.CALL_FAILED else "completed"
            self.calls_total.labels(t, outcome).inc()
            ended = self._spans.pop(ev.call_id, None)
            if ended is not None:
                span = ended
                self.active_calls.labels(t).dec()
                summary = ev.payload.get("latency")
                if isinstance(summary, dict):
                    for k, v in summary.items():
                        if isinstance(v, int | float):
                            span.set_attribute(f"parlio.latency.{k}", float(v))
                    p95 = summary.get("p95_s")
                    if isinstance(p95, int | float):
                        self.turn_p95.observe(float(p95))
                if ev.type == CallEventType.CALL_FAILED:
                    span.set_status(StatusCode.ERROR, str(ev.payload.get("reason", "failed")))
                span.end()

    def metrics_text(self) -> bytes:
        return generate_latest(self.registry)

    async def latency_report(
        self, store: CallStore, tenant_id: str | None, days: int = 7
    ) -> LatencyReport:
        until = datetime.now(UTC)
        since = until - timedelta(days=days)
        calls = await store.filter_calls(
            CallFilter(tenant_id=tenant_id, since=since, until=until, limit=20000)
        )
        groups: dict[str, list[Any]] = defaultdict(list)
        by_asst: dict[str, list[Any]] = defaultdict(list)
        for c in calls:
            groups[c.started_at.date().isoformat()].append(c)
            by_asst[c.assistant_id].append(c)
        return LatencyReport(
            tenant_id=tenant_id,
            since=since,
            until=until,
            target_turn_s=self.target_turn_s,
            overall=self._bucket(calls),
            per_day={k: self._bucket(v) for k, v in sorted(groups.items())},
            per_assistant={k: self._bucket(v) for k, v in by_asst.items()},
        )

    def _bucket(self, calls: list[Any]) -> LatencyBucket:
        answer = [c.answer_latency_s for c in calls if c.answer_latency_s is not None]
        p50 = [c.latency["p50_s"] for c in calls if isinstance(c.latency.get("p50_s"), int | float)]
        p95 = [c.latency["p95_s"] for c in calls if isinstance(c.latency.get("p95_s"), int | float)]

        def col(key: str) -> list[float]:
            return [
                float(c.latency[key]) for c in calls if isinstance(c.latency.get(key), int | float)
            ]

        return LatencyBucket(
            calls=len(calls),
            answered=sum(1 for c in calls if c.answered_at is not None),
            answer_p50_s=_pct(answer, 0.5),
            answer_p95_s=_pct(answer, 0.95),
            turn_p50_s=_avg(p50),
            turn_p95_s=_pct(p95, 0.95),
            eou_avg_s=_avg(col("avg_eou_s")),
            llm_ttft_avg_s=_avg(col("avg_llm_ttft_s")),
            tts_ttfb_avg_s=_avg(col("avg_tts_ttfb_s")),
            slow_calls=sum(1 for v in p95 if v > self.target_turn_s),
        )


# -- rate limiting --------------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window limiter keyed by tenant (dashboard) or key/IP (worker, public).

    In-process by design: each API replica enforces its own share, which is fine for the
    dashboard traffic we protect against (runaway scripts, scraped share links). Call concurrency
    limits are enforced separately via plan admission.
    """

    def __init__(self, per_minute: int, telemetry: Telemetry | None = None) -> None:
        self.per_minute = per_minute
        self.overrides: dict[str, int] = {}  # per-key limits set by platform staff
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._telemetry = telemetry

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Returns (allowed, remaining)."""
        t = now if now is not None else time.monotonic()
        q = self._hits[key]
        while q and t - q[0] > 60:
            q.popleft()
        limit = self.overrides.get(key, self.per_minute)
        if len(q) >= limit:
            if self._telemetry is not None:
                self._telemetry.rate_limited.labels(key).inc()
            return False, 0
        q.append(t)
        return True, limit - len(q)


# -- audit log -------------------------------------------------------------------------------------


class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    tenant_id: str
    actor: str  # user email, "worker", or "system"
    action: str  # e.g. "assistant.publish", "member.remove", "gdpr.erase"
    target: str | None = None
    method: str | None = None
    path: str | None = None
    status: int | None = None
    ip: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditLog:
    KIND = "audit"

    def __init__(self, store: CallStore) -> None:
        self.store = store

    async def record(self, entry: AuditEntry) -> AuditEntry:
        await self.store.put_doc(
            TenantDoc(
                kind=self.KIND,
                id=entry.id,
                tenant_id=entry.tenant_id,
                data=entry.model_dump(mode="json"),
                created_at=entry.at,
                updated_at=entry.at,
            )
        )
        return entry

    async def recent(self, tenant_id: str, limit: int = 200) -> list[AuditEntry]:
        docs = await self.store.list_docs(self.KIND, tenant_id, limit=limit)
        entries = [AuditEntry.model_validate(d.data) for d in docs]
        entries.sort(key=lambda e: e.at, reverse=True)
        return entries[:limit]


def action_for(method: str, path: str) -> str:
    """Map a mutating dashboard request to a dotted audit action, e.g. POST /v1/assistants ->
    assistants.create, DELETE /v1/telephony/trunks/{id} -> telephony.trunks.delete."""
    parts = [p for p in path.split("/") if p and p != "v1"]
    nouns = [p for p in parts if not _looks_like_id(p)]
    verb = {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}.get(
        method, method.lower()
    )
    if nouns and nouns[-1] in {
        "publish",
        "rollback",
        "test",
        "test-call",
        "rotate",
        "refresh",
        "checkout",
        "erase",
        "export",
        "pause",
        "resume",
        "invite",
    }:
        verb = nouns.pop().replace("-", "_")
    return ".".join([*nouns, verb]) if nouns else verb


def _looks_like_id(p: str) -> bool:
    # Resource nouns are lowercase words (optionally hyphenated); anything with a digit, '+' or
    # '_' is a path parameter (call ids, E.164 numbers, ten_/cs_ refs, user-chosen slugs).
    return any(ch.isdigit() or ch in "+_" for ch in p)
