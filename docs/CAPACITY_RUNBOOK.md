# Capacity runbook

Sizing assumptions, what to watch, and the exact action to take when a trigger fires. Growth is
configuration and purchases, not re-architecture: every step below is a replica count, node,
channel order or vendor tier.

## Reference numbers

| Metric | Launch (200 tenants) | Trigger to act | Enterprise reference |
|---|---|---|---|
| Concurrent calls (peak) | ~30 | see triggers | 1,000 |
| Calls / day | ~3,000 | — | 100,000 |
| Minutes / month | ~90k | — | 1.5M |
| Voice workers | 1 node, 4 vCPU (~20 calls) | >70% CPU for 10 min | 60–100 vCPU across regions |
| LiveKit | 1 node (single VPS) | 300 tenants or 100 concurrent | 2–3 nodes |
| SIP channels (Telnyx) | 40 | 150 tenants or 80% peak use | 1,200 |
| Postgres | shared container | p95 query > 50 ms or > 50 GB | managed primary + read replica + pgBouncer |
| Analytics read model | Postgres views | 1,000 concurrent / 10M calls | ClickHouse |

Rule of thumb per tenant: 0.15 concurrent calls at peak, 15 calls/day, 450 min/month.
Load test (`uv run parlio-load`) defaults to 60 concurrent calls = 2x launch peak.

## Signals

All from `/metrics` (Prometheus) and the `/v1/observability/latency` report; alert thresholds in
brackets are "act within a day", double them for "act now".

- `parlio_active_calls` (sum over tenants) vs the `max_concurrent_calls` sum of active plans.
- `parlio_turn_p95_seconds` histogram p95 > 1.5 s for 15 min (worker CPU, STT/LLM/TTS vendor).
- `parlio_answer_latency_seconds` p95 > 1.0 s (SIP edge / LiveKit / worker cold start).
- `parlio_rate_limited_total` rising for a tenant that is not abusive (raise plan limit).
- `parlio_calls_total{outcome="failed"}` > 1% of calls over 15 min.
- Load test ingest p95 (`parlio-load` report) > 250 ms (API / Postgres).
- Telnyx portal: channel utilisation > 80% at peak, `SIP 503` from the carrier.
- Postgres: partition size, `pg_stat_statements` p95, connection count > 80% of max.
- VPS: CPU > 70%, memory > 80%, disk > 70% (recordings), LiveKit `rooms_active`.

## Triggers and actions

### T1 — 100 tenants / 20 concurrent: split the single VPS

Currently API, worker, LiveKit, SIP, Postgres and Redis share one 4 GB droplet.

1. Move Postgres to a managed instance (DO Managed Postgres London, 2 vCPU/4 GB); restore from
   dump, switch `PARLIO_DATABASE_URL`, run `alembic upgrade head`.
2. Resize droplet to 8 GB or move the voice worker to its own 4 vCPU box (worker is the CPU
   consumer: VAD + turn detector per call).
3. Enable OTLP export (`PARLIO_OTLP_ENDPOINT`) to Grafana Cloud/Tempo so per-call traces
   survive a node rebuild.

### T2 — 150 tenants / 25 concurrent: order SIP capacity

1. Telnyx → Connections → `parlio-*-livekit-sip` → raise channel limit to 2x forecast peak
   (channels are billed monthly; lead time is hours).
2. Buy the next block of UK numbers into inventory (regulatory approval per block takes 1–2
   business days; keep 20% spare so provisioning never waits on Ofcom paperwork).
3. Enable the second carrier profile (Twilio) in `TelephonyProvider` failover config and run the
   carrier-failover drill.

### T3 — 300 tenants / 50 concurrent: second LiveKit node + worker pool

1. Add a second LiveKit node (same region) behind the existing Caddy/SIP edge; enable LiveKit
   Redis-backed multi-node routing (`redis` block in `livekit.yaml`).
2. Run voice workers as a pool of 2+ nodes (each ~20 calls on 4 vCPU); the worker is stateless
   so this is `docker compose up --scale` / Helm `replicaCount`.
3. Move recordings egress to object storage (DO Spaces / S3) with the retention job's
   `recording_days` lifecycle rule mirrored on the bucket.
4. Put pgBouncer in front of Postgres (transaction pooling; API and workers use short
   transactions already).

### T4 — 600 tenants / 100 concurrent: separate regions and read paths

1. Dedicated SIP edge pair (livekit-sip x2 + RTPengine) with anycast/DNS failover; Telnyx
   connection gets both edges as FQDN targets.
2. Postgres read replica; point analytics and dashboard list endpoints at it
   (`PARLIO_DATABASE_READ_URL`).
3. Post-call pipeline moves from in-process tasks to a worker service consuming the Redis stream;
   scale by consumer count.
4. Upgrade vendor tiers: Deepgram/Cartesia enterprise (concurrency), OpenAI scale tier
   (TPM headroom ≥ 3x measured peak).

### T5 — 1,000 concurrent: enterprise step-up (Part G of the build plan)

1. ClickHouse for the analytics read model (same `/v1/analytics` API; swap the store backend).
2. NATS/Kafka replaces Redis Streams for the event bus.
3. Active-active UK + US, isolated pools for Enterprise/Sovereign tenants.
4. Chaos testing and quarterly load test at 2x forecast peak.

## Rate limits and plan caps

- Per-tenant API rate limits (`RateLimiter`) default 600 req/min; raise for enterprise plans via
  `Plan.features` rather than globally.
- `Plan.max_concurrent_calls` is the tenant-level cap; the worker returns busy tone above it.
  Sum of active plans' caps is the theoretical peak — if it exceeds 80% of provisioned SIP
  channels + worker capacity, act on T2/T3 regardless of measured load.

## Cost guardrails

Vendor cost per minute is tracked in `UsageSummary.vendor_cost_pence`; gross margin per tenant is
on the Billing page. Investigate any tenant below 60% margin (usually long silent calls or a
runaway loop) before buying capacity.

## Running the load test

```bash
# against a staging stack (memory or Postgres store)
PARLIO_API_URL=https://api.staging.example PARLIO_WORKER_KEY=... \
  uv run parlio-load --concurrency 60 --calls 600 --tenants 20

# SLOs: ingest p95 <= 250 ms, error rate <= 0.1%, no 429s on the worker path
```

CI runs a reduced profile (20 concurrent / 100 calls) against an in-process API on every PR as a
regression guard; run the full 2x profile before each release and record the JSON report in the
release notes.
