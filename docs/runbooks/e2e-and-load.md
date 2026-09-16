# E2E, latency and load testing (Part F)

Three layers, from cheapest to most realistic. Only the last one needs a person — see
`live-call-test.md`.

| Layer | What it proves | Where | Command |
|---|---|---|---|
| Deterministic journeys | Every flow end-to-end against the real API, memory (and Postgres in CI) store, simulated bridge/SMS/calendar | CI on every PR | `uv run --package parlio-api pytest apps/api/tests/test_e2e_scenarios.py` |
| Deployed runner | The live stack answers, routes the DDI, holds a synthetic conversation (real LLM), ingests worker events under concurrency, runs post-call, and the Calls/Tickets/Analytics/Insights read models agree; cleans up after itself | Against the droplet, after every deploy / before a live-call session | `uv run --package parlio-e2e parlio-e2e --base-url https://api.<host> --worker-key $KEY --tenant demo --number +442046206823 --concurrency 10 --json` |
| Control-plane load | API ingest latency / error rate at N concurrent calls | Droplet, ad hoc | `uv run --package parlio-load-test parlio-load --base-url https://api.<host> --worker-key $KEY --concurrency 60 --calls 300 --tenants 1 --tenant demo` |
| Media latency | Answer / EOU / LLM TTFT / TTS TTFB per real call | Read from `GET /v1/observability/latency` (also in the runner's `latency report` step) and Platform admin → Ops | — |
| Real phones / handsets / SMS | Forwarding, PBX/BYO SIP, owner SMS, reminders, warm transfer to a human | Person with a phone | `live-call-test.md` |

Worker key: `grep PARLIO_WORKER_API_KEY /opt/parlio/infra/vps/.env` on the droplet. Never paste it into
a PR or chat.

`parlio-e2e` exits non-zero on any failed step; `--keep` leaves the synthetic calls in place (useful
before a live session so the read models are populated); without it the run erases its own caller
via the compliance API. It only exercises the API surface — it does **not** place PSTN/SIP calls or
send real SMS.

Load-test calls are named `load-*`; they are not erased automatically. Purge them from the demo
tenant afterwards (`delete from transcripts/call_events/calls where … like 'load-%'`) or run
`--tenants N` to spread them over throwaway tenants.

## Baseline — 2 vCPU / 4 GB droplet, single uvicorn process (Sept 2026)

Deployed runner, concurrency 10, DDI `+442046206823` → assistant `demo`: all 13 steps pass.

| Measure | Value |
|---|---|
| Event ingest (runner, 90 events, client in EU) | p50 191 ms · p95 662 ms |
| Post-call pipeline (summary + QA, real LLM) | 6.5 s |
| Answer time (20 real calls, `live_call_latency`) | p50 1.32 s · p95 2.08 s |
| Turn latency | p50 2.70 s · p95 4.66 s |
| Stage averages | EOU 1.71 s · LLM TTFT 0.95 s · TTS TTFB 0.14 s |
| Load, 30 concurrent (launch peak), 150 calls | ingest p50 235 ms · p95 1.35 s · 0 errors |
| Load, 60 concurrent (2× peak), 300 calls | ingest p50 684 ms · p95 1.84 s · 1 error (0.04 %) |

Reading it:
- Turn latency is dominated by **end-of-utterance detection** (1.7 s of the 2.7 s), not the vendors
  (TTS starts in 140 ms, the LLM in under a second). `TurnTuning.max_endpointing_delay` therefore
  moved from 2.0 s → 1.2 s (default in `parlio_voice/models.py`, and applied to the stored `demo` /
  `parlio-support` configs on the droplet — stored assistant configs pin their own value, so a
  code default alone changes nothing for existing tenants). Re-measure after the next 20 real
  calls; if callers get cut off mid-sentence, raise it towards 1.5 s per assistant in Studio.
- Ingest p95 breaches the 250 ms capacity SLO at ≥ 30 concurrent because the API is one uvicorn
  process sharing 2 vCPU with the voice worker, LiveKit and Postgres. That is fine for the dev
  droplet (the worker cannot run 30 media sessions on it anyway) — for production follow
  `docs/CAPACITY_RUNBOOK.md`: API on its own node, then `--workers` once live/WS state is on Redis.
- GB vs US profiles: the deterministic suite covers both region profiles (timezone, number
  formats, `America/New_York` analytics buckets). Per-region *media* tuning needs a US-hosted worker
  and number; no US measurements exist yet — record them here when a US tenant is onboarded.

## After every deploy
1. `parlio-e2e … --json > runs/<date>.json` — must pass.
2. Compare `latency report` with the table above; a p95 regression > 20 % is runbook 2 in `README.md`.
3. Before a live-call session, run with `--keep` so Analytics/Insights are populated.
