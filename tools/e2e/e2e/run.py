"""Deployed-environment E2E runner.

Drives the same journeys as `apps/api/tests/test_e2e_scenarios.py` against a *running* Parlio
stack (the VPS, staging, or a local compose) using only its public/worker HTTP surface:

* health + assistant resolution for the tenant's Parlio number
* the platform's synthetic call (scripted conversation through the real text agent / LLM)
* web chat through the public widget endpoint (real AI reply)
* N simulated calls reported the way the voice worker does (started -> answered -> transcript
  -> ticket -> ended), then waits for the post-call pipeline and checks Calls, Tickets,
  Analytics, Insights and the latency report all agree
* prints the tenant's real-call latency report (answer / turn p50 & p95 from live traffic)
* erases the synthetic caller afterwards through the GDPR endpoint (unless --keep)

What it deliberately does NOT do: place PSTN/SIP calls or send real SMS - those need a human
with a handset; see docs/runbooks/live-call-test.md.

    uv run parlio-e2e --base-url https://api.example --worker-key $PARLIO_WORKER_API_KEY \
        --tenant demo --user owner@demo.parlio.local --concurrency 5

Exit status is non-zero when any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import httpx

Param = str | int | float | bool | None
E2E_CALLER = "+447700900999"  # Ofcom drama range: never a real subscriber
TURNS = [
    ("Hi, do you do boiler servicing?", "Yes, we service all makes of boiler."),
    ("Great, can someone call me back about a quote?", "Of course, I'll take some details."),
    ("It's Sam Test, oh seven seven double-oh nine double-oh nine nine nine.", "Thank you Sam."),
]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    ms: float = 0.0


@dataclass
class Report:
    base_url: str
    tenant: str
    checks: list[Check] = field(default_factory=list)
    ingest_ms: list[float] = field(default_factory=list)
    postcall_s: list[float] = field(default_factory=list)
    latency_report: dict[str, object] = field(default_factory=dict)

    def add(self, name: str, passed: bool, detail: str = "", ms: float = 0.0) -> None:
        self.checks.append(Check(name, passed, detail, ms))
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {name:<34} {detail}", flush=True)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def summary(self) -> dict[str, object]:
        def pct(xs: list[float], q: float) -> float | None:
            if not xs:
                return None
            ys = sorted(xs)
            return round(ys[min(len(ys) - 1, int(q * (len(ys) - 1)))], 3)

        return {
            "base_url": self.base_url,
            "tenant": self.tenant,
            "passed": self.ok,
            "checks": [c.__dict__ for c in self.checks],
            "event_ingest_ms": {
                "n": len(self.ingest_ms),
                "p50": pct(self.ingest_ms, 0.5),
                "p95": pct(self.ingest_ms, 0.95),
                "max": round(max(self.ingest_ms), 1) if self.ingest_ms else None,
            },
            "postcall_s": {
                "n": len(self.postcall_s),
                "p50": pct(self.postcall_s, 0.5),
                "p95": pct(self.postcall_s, 0.95),
            },
            "live_call_latency": self.latency_report,
        }


class Api:
    def __init__(
        self, base_url: str, tenant: str, worker_key: str, user: str | None, token: str | None
    ) -> None:
        self.tenant = tenant
        self.q = {"tenant_id": tenant}
        self.worker = {"X-Worker-Key": worker_key}
        dash: dict[str, str] = {}
        if token:
            dash["Authorization"] = f"Bearer {token}"
        if user:
            dash["X-Parlio-User"] = user
        self.dash = dash
        self.http = httpx.AsyncClient(base_url=base_url, timeout=60)

    async def get(self, path: str, **params: Param) -> httpx.Response:
        return await self.http.get(path, params={**self.q, **params}, headers=self.dash)

    async def post(self, path: str, body: object | None = None, **params: Param) -> httpx.Response:
        return await self.http.post(path, params={**self.q, **params}, json=body, headers=self.dash)

    async def worker_get(self, path: str, **params: Param) -> httpx.Response:
        return await self.http.get(path, params=params, headers=self.worker)

    async def worker_post(self, path: str, body: object, **params: Param) -> httpx.Response:
        return await self.http.post(path, params=params, json=body, headers=self.worker)


def _event(kind: str, call_id: str, cfg: dict[str, object], payload: dict[str, object]) -> dict:
    return {
        "event_id": uuid4().hex,
        "type": kind,
        "call_id": call_id,
        "tenant_id": cfg["tenant_id"],
        "company_id": cfg["company_id"],
        "assistant_id": cfg["assistant_id"],
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }


async def _timed(coro: object) -> tuple[httpx.Response, float]:
    t0 = time.perf_counter()
    r = await coro  # type: ignore[misc]
    return r, (time.perf_counter() - t0) * 1000


# -- steps ------------------------------------------------------------------------------------


async def step_health(api: Api, rep: Report) -> None:
    r, ms = await _timed(api.http.get("/healthz"))
    rep.add("health", r.status_code == 200, f"{r.status_code} in {ms:.0f}ms", ms)


async def step_resolve(api: Api, rep: Report, number: str | None) -> dict[str, object] | None:
    r = await api.get("/v1/assistants")
    if r.status_code != 200 or not r.json():
        rep.add("assistants", False, f"{r.status_code} {r.text[:120]}")
        return None
    cfg: dict[str, object] = r.json()[0]
    target = number
    if not target:
        rn = await api.get("/v1/numbers")
        nums = [n["e164"] for n in rn.json()] if rn.status_code == 200 else []
        target = nums[0] if nums else None
    if not target:
        rep.add(
            "resolve",
            True,
            "no provisioned number; pass --number <DDI> to exercise routing. Using first assistant",
        )
        cfg["dialed"] = "+440000000000"
        return cfg
    r2, ms = await _timed(api.worker_get("/v1/worker/assistants/resolve", number=target))
    ok = r2.status_code == 200 and r2.json()["tenant_id"] == api.tenant
    rep.add(
        "resolve", ok, f"{target} -> {r2.json().get('assistant_id') if ok else r2.text[:80]}", ms
    )
    out: dict[str, object] = r2.json() if ok else cfg
    out["dialed"] = target
    return out


async def step_synthetic(api: Api, rep: Report) -> None:
    r, ms = await _timed(api.post("/v1/health/synthetic"))
    if r.status_code != 200:
        rep.add("synthetic call", False, f"{r.status_code} {r.text[:120]}", ms)
        return
    run = r.json()
    failed = [f"{c['path']}: {c['detail']}" for c in run["checks"] if not c["passed"]]
    rep.add(
        "synthetic call",
        bool(run["passed"]),
        " | ".join(failed) if failed else f"{len(run['checks'])} checks in {run['duration_ms']}ms",
        ms,
    )


async def step_webchat(api: Api, rep: Report) -> None:
    r = await api.get("/v1/inbox/widget")
    if r.status_code != 200:
        rep.add("web chat", False, f"widget {r.status_code}")
        return
    token = r.json()["token"]
    visitor = f"e2e-{uuid4().hex[:12]}"
    r2, ms = await _timed(
        api.http.post(
            f"/v1/public/chat/{token}/messages",
            json={"visitor": visitor, "text": "What are your opening hours?", "name": "E2E"},
        )
    )
    msgs = r2.json() if r2.status_code == 200 else []
    ai = [m for m in msgs if m.get("author") == "ai" and m.get("direction") == "out"]
    rep.add(
        "web chat",
        r2.status_code == 200 and bool(ai),
        (ai[-1]["text"][:90] if ai else f"{r2.status_code} {r2.text[:90]}"),
        ms,
    )


async def simulated_call(api: Api, rep: Report, cfg: dict[str, object], n: int) -> str | None:
    call_id = f"e2e-{uuid4().hex[:10]}"
    dialed = str(cfg["dialed"])

    async def emit(kind: str, payload: dict[str, object]) -> bool:
        r, ms = await _timed(
            api.worker_post("/v1/worker/events", _event(kind, call_id, cfg, payload))
        )
        rep.ingest_ms.append(ms)
        return r.status_code == 202

    ok = await emit(
        "call.started", {"caller": E2E_CALLER, "dialed": dialed, "sip": {"mode": "e2e"}}
    )
    ok &= await emit("call.answered", {"answer_latency_s": 0.3 + 0.01 * n})
    for user, agent in TURNS:
        ok &= await emit("call.transcript_item", {"role": "user", "text": user})
        ok &= await emit("call.transcript_item", {"role": "assistant", "text": agent})
    t = await api.worker_post(
        "/v1/worker/tickets",
        {
            "call_id": call_id,
            "caller_name": "Sam Test",
            "caller_number": E2E_CALLER,
            "reason": f"E2E boiler service quote #{n}",
            "priority": "normal",
            "department": "bookings",
            "callback_window": "tomorrow morning",
        },
        tenant_id=str(cfg["tenant_id"]),
        company_id=str(cfg["company_id"]),
    )
    ok &= t.status_code == 201
    ok &= await emit("call.ended", {"reason": "caller_hangup", "duration_s": 75})
    if not ok:
        rep.add(f"call {n}", False, f"event/ticket rejected (last {t.status_code} {t.text[:80]})")
        return None
    return call_id


async def wait_postcall(api: Api, call_id: str, timeout_s: float) -> dict[str, object] | None:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        r = await api.get(f"/v1/calls/{call_id}")
        if r.status_code == 200:
            rec: dict[str, object] = r.json()
            if rec.get("status") == "completed" and rec.get("summary"):
                return rec
        await asyncio.sleep(1)
    return None


async def step_calls(
    api: Api, rep: Report, cfg: dict[str, object], concurrency: int, timeout_s: float
) -> list[str]:
    ids = [
        c
        for c in await asyncio.gather(
            *(simulated_call(api, rep, cfg, i) for i in range(concurrency))
        )
        if c
    ]
    rep.add(
        "simulated calls ingested",
        len(ids) == concurrency,
        f"{len(ids)}/{concurrency}; ingest p95 {rep.summary()['event_ingest_ms']['p95']}ms",  # type: ignore[index]
    )
    t0 = time.perf_counter()
    recs = await asyncio.gather(*(wait_postcall(api, c, timeout_s) for c in ids))
    done = [r for r in recs if r]
    rep.postcall_s.append(time.perf_counter() - t0)
    ticketed = sum(1 for r in done if r.get("ticket_ids"))
    rep.add(
        "post-call pipeline",
        len(done) == len(ids) and ticketed == len(ids),
        f"{len(done)}/{len(ids)} summarised, {ticketed} linked to a ticket, "
        f"{time.perf_counter() - t0:.1f}s",
    )
    if done:
        first = done[0]
        transcript = first.get("transcript")
        n_items = len(transcript) if isinstance(transcript, list) else 0
        rep.add(
            "call detail",
            first.get("caller") == E2E_CALLER and n_items == 2 * len(TURNS),
            f"{first.get('kind')} / {n_items} transcript items",
        )
    return ids


async def step_readmodels(api: Api, rep: Report, ids: list[str]) -> None:
    r = await api.get("/v1/calls", kind="ticketed", limit=200)
    listed = {c["call_id"] for c in r.json()} if r.status_code == 200 else set()
    rep.add("calls list", set(ids) <= listed, f"{len(set(ids) & listed)}/{len(ids)} visible")

    r = await api.get("/v1/tickets")
    tk = [t for t in r.json() if t.get("call_id") in ids] if r.status_code == 200 else []
    rep.add("tickets", len(tk) == len(ids), f"{len(tk)} open tickets for e2e calls")

    r = await api.get("/v1/analytics/overview", days=1)
    ok = r.status_code == 200 and r.json()["current"]["total_calls"] >= len(ids)
    total = r.json().get("current", {}).get("total_calls") if r.status_code == 200 else None
    rep.add("analytics overview", ok, f"total_calls={total} ({r.status_code})")

    r = await api.get("/v1/analytics/insights", days=30)
    rep.add("insights", r.status_code == 200 and bool(r.json().get("demand")), f"{r.status_code}")

    r = await api.get("/v1/observability/latency", days=7)
    if r.status_code == 200:
        rep.latency_report = r.json().get("overall", {})
        o = rep.latency_report
        rep.add(
            "latency report",
            True,
            f"answered={o.get('answered')} "
            f"answer p50/p95={o.get('answer_p50_s')}/{o.get('answer_p95_s')}s "
            f"turn p50/p95={o.get('turn_p50_s')}/{o.get('turn_p95_s')}s slow={o.get('slow_calls')}",
        )
    else:
        rep.add("latency report", False, f"{r.status_code}")


async def step_cleanup(api: Api, rep: Report, ids: list[str]) -> None:
    r = await api.post("/v1/compliance/erase", {"e164": E2E_CALLER})
    ok = r.status_code == 200 and r.json().get("calls_purged", 0) >= len(ids)
    rep.add("cleanup (GDPR erase)", ok, r.text[:120])


async def run(args: argparse.Namespace) -> Report:
    api = Api(args.base_url, args.tenant, args.worker_key, args.user, args.token)
    rep = Report(args.base_url, args.tenant)
    async with api.http:
        await step_health(api, rep)
        cfg = await step_resolve(api, rep, args.number)
        if cfg is None:
            return rep
        if not args.skip_synthetic:
            await step_synthetic(api, rep)
        await step_webchat(api, rep)
        ids = await step_calls(api, rep, cfg, args.concurrency, args.postcall_timeout)
        if ids:
            await step_readmodels(api, rep, ids)
        if not args.keep:
            await step_cleanup(api, rep, ids)
    return rep


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base-url", default=os.environ.get("PARLIO_API_URL", "http://localhost:8000"))
    p.add_argument(
        "--worker-key", default=os.environ.get("PARLIO_WORKER_API_KEY", "dev-worker-key")
    )
    p.add_argument("--tenant", default=os.environ.get("PARLIO_E2E_TENANT", "demo"))
    p.add_argument(
        "--user", default=os.environ.get("PARLIO_E2E_USER"), help="X-Parlio-User (dev auth)"
    )
    p.add_argument(
        "--token", default=os.environ.get("PARLIO_E2E_TOKEN"), help="Supabase bearer token"
    )
    p.add_argument("--number", help="Parlio number to resolve (defaults to the assistant's first)")
    p.add_argument("--concurrency", type=int, default=3, help="simultaneous simulated calls")
    p.add_argument("--postcall-timeout", type=float, default=90.0)
    p.add_argument("--skip-synthetic", action="store_true", help="skip the LLM synthetic call")
    p.add_argument("--keep", action="store_true", help="leave the e2e caller's data in place")
    p.add_argument("--json", help="write the report here")
    args = p.parse_args()

    rep = asyncio.run(run(args))
    summary = rep.summary()
    print(json.dumps({k: v for k, v in summary.items() if k != "checks"}, indent=2))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(summary, f, indent=2)
    sys.exit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
