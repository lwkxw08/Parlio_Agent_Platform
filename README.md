# ParlioTec

Proprietary, low-latency, UK-first AI phone assistant platform. Phase 1 of the
[build plan](docs/BUILD_PLAN.md): monorepo, LiveKit Agents voice worker, Core API, Telnyx
carrier adapter, dashboard on Cloudflare Pages, local/K8s/Terraform infra, latency harness.

```
PSTN / customer PBX  ->  Telnyx SIP  ->  LiveKit SIP  ->  LiveKit Agents worker (apps/voice-worker)
                                                              |  Silero VAD + turn detector
                                                              |  Deepgram STT -> LLM -> Cartesia/ElevenLabs TTS
                                                              v
                                          Core API (apps/api, FastAPI)  <-  Redis Streams call events
                                                              v
                                          Dashboard (apps/web, Next.js on Cloudflare Pages)
```

## Layout

| Path | What |
|------|------|
| `apps/voice-worker` | `parlio_voice` - LiveKit Agents worker: SIP entry, tenant config, STT/LLM/TTS chains with region profiles + fallback, barge-in, consent + greeting, egress recording, per-turn latency metrics, call events |
| `apps/api` | `parlio_api` - FastAPI Core API: assistant resolution, call-event ingestion/folding, dashboard endpoints, `TelephonyProvider` abstraction (Telnyx first; Twilio/others drop in for failover) |
| `apps/web` | Next.js dashboard (overview, calls, transcript) deployed via OpenNext to Cloudflare Pages/Workers - preview URL per branch |
| `tools/latency-harness` | Offline simulated-call harness (`parlio-latency`) + `--real` mode against vendor APIs |
| `infra/local` | Docker Compose: Redis, Postgres, MinIO, LiveKit, LiveKit SIP, API, worker; SIP trunk/dispatch JSON |
| `infra/helm/parlio` | Helm chart: worker (HPA on active calls, call-draining), API, SIP bridge, LiveKit + Redis deps |
| `infra/terraform` | dev / staging / production roots; telephony (Telnyx) + dashboard (Cloudflare Pages) modules, other modules stubbed for Phase 6 |

## Local development

```sh
uv sync                                  # Python workspace (3.12+)
uv run pytest -q                         # 21 tests incl. simulated call
uv run ruff check . && uv run mypy apps/voice-worker/parlio_voice apps/api/parlio_api tools/latency-harness/latency_harness
uv run parlio-latency --turns 5          # offline turn-latency report (no vendor keys)

cp .env.example .env                     # fill vendor keys for real calls
uv run uvicorn parlio_api.main:app --reload            # API on :8000
uv run parlio-voice dev                                # worker (needs LiveKit + keys)
cd apps/web && npm install && npm run dev              # dashboard on :3000

docker compose -f infra/local/docker-compose.yml up    # full local stack
```

Answering a real call locally: point a Telnyx number at the SIP bridge, then create the inbound
trunk and dispatch rule in `infra/local/sip/` with `lk` (see that README).

## Environments & releases

`dev` (always-on test number + Pages previews per branch) -> `staging` (prod replica, staging
numbers) -> `production`. CI runs lint/types/tests/simulated
call/docker builds on every PR and deploys the dashboard preview to Cloudflare. Voice workers roll with a 30-min drain so in-flight
calls finish on the old version. See `docs/BUILD_PLAN.md` for canary/feature-flag/prompt-versioning.

## Region profiles

`standard` (any provider), `sovereign-uk` (Deepgram EU, Azure OpenAI UK, ElevenLabs EU only),
`sovereign-uk-strict` (self-hosted models - Phase 16). Enforced per assistant in
`parlio_voice/providers.py`.

Secrets are never committed; local compose uses dev-only placeholder credentials.
