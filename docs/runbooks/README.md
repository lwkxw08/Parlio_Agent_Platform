# Parlio operations runbooks

Alerts come from the Phase 17 health engine (`apps/api/parlio_api/ops.py`), surface on **Platform admin → Ops**, and page the on-call (email / SMS to the rota, or PagerDuty / Opsgenie / webhook) for `critical`. Because the health engine runs inside the API, "the API is down" is watched from outside: DigitalOcean Uptime checks (eu_west + us_east) on `api.parliotec.com/healthz`, `app.parliotec.com`, `parliotec.com`, `lk.parliotec.com` and `api.staging.parliotec.com`, each with Down (2 min), SSL-expiry (<14 days) and latency (>3 s) alerts emailed to the DigitalOcean account owner. Every runbook follows: **detect → confirm → mitigate → communicate → RCA**.

## Severities & SLA targets

| Priority | Definition | Ack | Status-page update | RCA |
|---|---|---|---|---|
| P1 | Voice down / no calls answered for >1 tenant, SIP edge or carrier outage | 15 min, 24x7 | 30 min, then hourly | 48 h (Enterprise / Sovereign), 5 days otherwise |
| P2 | Degraded: latency p95 > 2.5 s, failed-call rate > 10 %, one tenant down | 1 h (business hours) | 2 h | 5 days |
| P3 | Single feature / integration broken, no call impact | 24 h | optional | — |

SLA tiers (`SlaTier`): Standard 99.9 % voice uptime, Enterprise 99.95 %. Service credits are computed per tenant by `OpsService.service_credit()` from status-page outage minutes — apply via Platform admin → Tenants → Credits.

## Runbooks

### 1. Answer rate collapse (`answer_rate` alert)
1. Confirm on Ops → Tenant health: is it one tenant (forwarding / trunk) or many (platform)?
2. Many tenants → check `docker compose ps` on the droplet, LiveKit `/healthz`, worker logs (`docker compose logs worker --tail 200`). Restart worker if it is not registering with LiveKit.
3. One tenant → run **Synthetic call**. Pass = customer forwarding is off → open a support ticket with the forwarding walkthrough. Fail = check the trunk (runbook 3).
4. Declare an incident on Ops → Status & incidents if >1 tenant is affected.

### 2. Latency regression (`latency` alert / canary gate "hold")
1. Ops overview → canary gate reasons. Do **not** roll out new worker images while it says hold.
2. Check vendor status (Deepgram, OpenAI, Cartesia). If one vendor is slow, switch the affected stage with `PARLIO_STT_PROVIDER` / `PARLIO_LLM_MODEL` / `PARLIO_TTS_PROVIDER` on the droplet and restart the worker.
3. If latency is platform-wide with healthy vendors, check droplet CPU/RAM (`htop`) and LiveKit node load; scale per `docs/CAPACITY_RUNBOOK.md`.
4. Roll back the last deploy if it coincides with the regression (`git revert` + merge → CI redeploys).

### 3. SIP trunk unhealthy (`sip` alert)
1. Ops → tenant detail → SIP trunks: registration state, OPTIONS RTT, INVITE/auth failures, MOS / jitter / loss.
2. Click **Diagnose** (fault classification). `customer_provider` / `customer_config` → reply on the ticket with the provider report (email the provider only with the customer's consent). `carrier` → raise with Telnyx, attach the evidence pack. `parlio` → escalate to engineering.
3. **Remediate** re-registers the trunk and, if still failing, falls back the tenant's numbers to plain forwarding so calls keep flowing.

### 4. Forwarding may be off (`forwarding` alert)
Baseline says the tenant should have received calls during opening hours and none arrived. Run a synthetic call; if it passes, the customer's forwarding is off — the support agent sends the per-carrier re-enable guide (BT `*21*<number>#`, mobiles `**21*<number>#`, etc.).

### 5. Carrier outage / failover
1. Status page shows `sip_edge` or `carrier` major outage; auto-failover flips `FailoverState.active` to the secondary carrier and pages on-call.
2. Verify inbound on the secondary; switching back is manual (Ops → On-call & failover) once the primary is stable for 30 min.

### 6. Synthetic call failed after deploy (`synthetic` alert)
A support ticket is opened automatically. Compare the failing check (`greeting` / `faq` / `booking` / `transfer`) with the deploy diff; roll back if the failure reproduces on the support tenant.

### 7. Integration sync failures (`connectors` alert)
Connectors tab → retry the failed sync job. Repeated auth failures mean the customer's OAuth token expired — ticket with "reconnect" instructions.

## Testing runbooks
- `e2e-and-load.md` — deterministic E2E suite, deployed `parlio-e2e` runner, load test, latency baseline and tuning notes.
- `live-call-test.md` — the human-run script for real phones, PBX/SIP, owner SMS, reminders and warm transfers, with the evidence to check.

## Communication
- Status page: Ops → Status & incidents (public at `/status` and `/v1/public/status-page`).
- Tenant banner: Ops → Banner for planned maintenance.
- P1 customers on Enterprise SLA get a direct email from the on-call within 30 min.

## RCA template
```
Title:
Severity:            Duration (UTC):            Tenants affected:
Impact:              (what customers saw, calls missed, minutes lost)
Timeline:            detect → ack → mitigate → resolve, with timestamps
Root cause:
Contributing factors:
What went well / badly:
Actions:             owner, due date (prevent, detect faster, mitigate faster)
Service credits:     tier, %, tenants
```
