"""Control-plane load test for the Parlio Core API.

Simulates N concurrent calls the way the voice worker reports them (started -> answered ->
transcript items -> ended) and measures event-ingest latency, error rate and post-call
throughput. It does *not* exercise media (LiveKit/SIP/STT/TTS) — that is the latency harness'
job — but it does load everything a call touches on the API side: event idempotency, call
store writes (Postgres partitions), post-call pipeline, telemetry, retention hooks.

Sizing: launch plan is ~200 tenants at ~0.15 concurrent calls each => ~30 concurrent calls at
peak. The default here is 2x that (60), per the Phase 6 acceptance criterion.

    uv run parlio-load --base-url https://api.example --worker-key ... --concurrency 60 --calls 600

Exit status is non-zero when any SLO is breached so it can gate a CI job or a release.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import httpx

PHRASES = [
    "Hi, I'd like to book a boiler service please.",
    "Can someone call me back about my invoice?",
    "What time do you close today?",
    "I'm having a problem with my heating, it's urgent.",
    "Do you cover the Manchester area?",
]


@dataclass
class Stats:
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    events: int = 0
    calls: int = 0
    rate_limited: int = 0

    def pct(self, q: float) -> float:
        if not self.latencies_ms:
            return 0.0
        xs = sorted(self.latencies_ms)
        return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))]


def event(
    kind: str, call_id: str, tenant: str, assistant: str, payload: dict[str, object]
) -> dict[str, object]:
    return {
        "event_id": uuid4().hex,
        "type": kind,
        "call_id": call_id,
        "tenant_id": tenant,
        "company_id": tenant,
        "assistant_id": assistant,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }


async def post_event(
    client: httpx.AsyncClient, headers: dict[str, str], ev: dict[str, object], stats: Stats
) -> None:
    t0 = time.perf_counter()
    try:
        r = await client.post("/v1/worker/events", json=ev, headers=headers)
        stats.latencies_ms.append((time.perf_counter() - t0) * 1000)
        stats.events += 1
        if r.status_code == 429:
            stats.rate_limited += 1
        elif r.status_code >= 400:
            stats.errors += 1
    except httpx.HTTPError:
        stats.errors += 1


async def simulate_call(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    tenant: str,
    assistant: str,
    turns: int,
    hold_s: float,
    stats: Stats,
) -> None:
    cid = f"load-{uuid4().hex[:10]}"
    caller = f"+4477009{random.randint(0, 99999):05d}"
    await post_event(
        client,
        headers,
        event(
            "call.started", cid, tenant, assistant, {"caller": caller, "dialed": "+440000000000"}
        ),
        stats,
    )
    await post_event(
        client,
        headers,
        event(
            "call.answered", cid, tenant, assistant, {"answer_latency_s": random.uniform(0.3, 0.7)}
        ),
        stats,
    )
    for i in range(turns):
        role = "user" if i % 2 == 0 else "assistant"
        text = random.choice(PHRASES) if role == "user" else "Of course, let me help with that."
        await post_event(
            client,
            headers,
            event("call.transcript_item", cid, tenant, assistant, {"role": role, "text": text}),
            stats,
        )
        await asyncio.sleep(hold_s / max(turns, 1))
    await post_event(
        client,
        headers,
        event(
            "call.ended",
            cid,
            tenant,
            assistant,
            {
                "reason": "hangup",
                "duration_s": hold_s + turns * 2.5,
                "latency": {
                    "turns": turns,
                    "p50_s": random.uniform(0.7, 1.1),
                    "p95_s": random.uniform(1.0, 1.6),
                },
            },
        ),
        stats,
    )
    stats.calls += 1


async def run(args: argparse.Namespace) -> int:
    headers = {"X-Worker-Key": args.worker_key}
    stats = Stats()
    sem = asyncio.Semaphore(args.concurrency)
    tenants = (
        [f"load-tenant-{i}" for i in range(args.tenants)] if args.tenants > 1 else [args.tenant]
    )

    async def one(i: int) -> None:
        async with sem:
            await simulate_call(
                client,
                headers,
                tenants[i % len(tenants)],
                args.assistant,
                args.turns,
                args.hold,
                stats,
            )

    limits = httpx.Limits(
        max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency
    )
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10.0, limits=limits) as client:
        health = await client.get("/healthz")
        health.raise_for_status()
        t0 = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(args.calls)))
        wall = time.perf_counter() - t0
        # post-call pipeline drains asynchronously; give it a bounded window then sample
        settled = 0
        deadline = time.perf_counter() + args.settle
        while time.perf_counter() < deadline:
            r = await client.get(
                "/v1/calls", params={"tenant_id": tenants[0], "limit": 200, "kind": "answered"}
            )
            if r.status_code == 200:
                settled = sum(1 for c in r.json() if c.get("summary"))
                if settled >= min(200, args.calls // len(tenants)):
                    break
            await asyncio.sleep(0.5)

    p50, p95, p99 = stats.pct(0.5), stats.pct(0.95), stats.pct(0.99)
    err_rate = stats.errors / max(stats.events, 1)
    eps = stats.events / max(wall, 1e-6)
    report = {
        "calls": stats.calls,
        "events": stats.events,
        "concurrency": args.concurrency,
        "wall_s": round(wall, 2),
        "events_per_s": round(eps, 1),
        "ingest_ms": {"p50": round(p50, 1), "p95": round(p95, 1), "p99": round(p99, 1)},
        "mean_ms": round(statistics.fmean(stats.latencies_ms), 1) if stats.latencies_ms else 0,
        "errors": stats.errors,
        "error_rate": round(err_rate, 4),
        "rate_limited": stats.rate_limited,
        "postcall_settled_sampled": settled,
    }
    print(json.dumps(report, indent=2))

    failures = []
    if p95 > args.slo_p95_ms:
        failures.append(f"ingest p95 {p95:.0f}ms > {args.slo_p95_ms}ms")
    if err_rate > args.slo_error_rate:
        failures.append(f"error rate {err_rate:.2%} > {args.slo_error_rate:.2%}")
    if stats.rate_limited and not args.allow_429:
        failures.append(f"{stats.rate_limited} requests rate-limited (worker path must be exempt)")
    if failures:
        print("SLO BREACH: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("SLOs met", file=sys.stderr)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base-url", default=os.environ.get("PARLIO_API_URL", "http://localhost:8000"))
    p.add_argument("--worker-key", default=os.environ.get("PARLIO_WORKER_KEY", "dev-worker-key"))
    p.add_argument(
        "--concurrency", type=int, default=60, help="simultaneous calls (2x launch peak)"
    )
    p.add_argument("--calls", type=int, default=600, help="total calls to simulate")
    p.add_argument("--turns", type=int, default=6, help="transcript items per call")
    p.add_argument("--hold", type=float, default=2.0, help="seconds a simulated call stays open")
    p.add_argument("--tenants", type=int, default=20, help="spread calls over N synthetic tenants")
    p.add_argument("--tenant", default="demo", help="tenant to use when --tenants 1")
    p.add_argument("--assistant", default="demo")
    p.add_argument("--settle", type=float, default=15.0, help="seconds to wait for post-call drain")
    p.add_argument("--slo-p95-ms", type=float, default=250.0)
    p.add_argument("--slo-error-rate", type=float, default=0.001)
    p.add_argument("--allow-429", action="store_true")
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
