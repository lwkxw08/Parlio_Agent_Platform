# Parlio — Proprietary AI Phone Assistant Platform: Full Build Plan

Session origin: https://app.devin.ai/sessions/f9e9c86b2c1d4fed908841a097d93578 (Sept 2026). Reference competitor analysed: app.getdena.com (Next.js 14 on Vercel, Supabase auth/DB, custom API on Google Cloud Run, voice via Vapi + ElevenLabs Conversational AI, GA + Facebook Pixel).

## Goals
- Own the whole voice pipeline (no Vapi/ElevenLabs Conversational AI) with sub-500ms turn latency and instant pick-up.
- Feature parity with Dena, plus: ticket/callback queue, outbound calling, live takeover, omnichannel inbox, payments, QA self-improvement loop, business & enterprise integrations, white-label.
- BYO SIP trunking: customers connect their own PBX or SIP account instead of call forwarding.
- UK Sovereign tier: per-tenant region profile keeping all data, compute, telephony and (optionally) AI models in the UK.
- Best-in-class support operations, with Parlio's own AI agents providing 24/7 first-line support and telephony fault diagnosis.
- Sized for 200+ tenants at launch, scale-ready to enterprise volumes by configuration (no re-architecture).
- Safe release engineering: dev -> staging -> production with canaries, flags and zero-downtime deploys from day one.
- Multi-tenant SaaS (organization -> companies -> users -> numbers -> assistants), UK GDPR-ready.
- Explicitly out of scope for now: dental/healthcare platform integrations (feasible later via the adapter framework).

## Architecture

```
Caller --PSTN/WhatsApp/Web--> Twilio/Telnyx number . Customer PBX/SIP trunk . WhatsApp . web widget
                     | (SIP trunk, 8kHz u-law / WebRTC for web calls)
                     v
            SIP edge (Kamailio/OpenSIPS: registrar, auth, TLS/SRTP) -> LiveKit SIP + LiveKit Agents (Python)
              |- VAD (Silero) + turn detection, barge-in
              |- STT: Deepgram Nova-3 streaming (multi-language auto-detect)
              |- LLM: GPT-4o-mini / Claude Haiku / Groq (streaming, tool calls)
              |- TTS: Cartesia Sonic / ElevenLabs Flash (streaming, cloned voices)
              '- Tools: book_appointment, send_sms, transfer_call, create_ticket,
                        hold_and_callback, take_payment, lookup_caller, lookup_faq,
                        capture_lead_fields, escalate_urgent, block_check
                     | events (event bus) + dual-channel audio -> object storage
                     v
            Core API (FastAPI, Python)
              |- Postgres (Supabase) + pgvector; tenant_id-partitioned; read model for analytics
              |- Redis (per-call state, pre-warmed configs, live-monitor pub/sub)
              |- Event bus (Redis Streams -> NATS/Kafka later) -> async workers:
              |     post-call summary/extraction/QA scoring, outbound dialer, digests,
              |     notifications, integration sync, synthetic test calls, SIP registration health
              |- Adapter layer: Booking/CRM/Ticketing/HR interfaces -> vendor plug-ins
              |- Provider layer: STT/LLM/TTS/telephony/storage by tenant region profile, multi-vendor failover
              '- Webhooks in: Twilio/Telnyx, Stripe, calendars, vendor systems, web forms
                     v
            Dashboard: Next.js 15 + Supabase Auth (white-label themable)
```

Why LiveKit Agents: SIP ingress, warm transfer/bridging, Egress dual-channel recording, participant injection (live listen/whisper/takeover), stateless horizontally-scalable workers, fully self-hostable. Pipecat is the fallback. Every STT/LLM/TTS layer is swappable.

Latency tactics: pre-dispatch worker on SIP INVITE (greeting <300ms after answer); config + embeddings cached in Redis; LLM streams into TTS sentence-by-sentence; endpointing ~200ms; workers co-located with carrier edge (UK/EU + US-east); Groq/Cerebras option for <150ms TTFT.

### Region profiles (per tenant)
| Layer | standard | sovereign-uk (L1) | sovereign-uk-strict (L2) |
|---|---|---|---|
| Compute | Kubernetes (any region) | AWS London / Azure UK South | UK provider or customer private cloud |
| DB | Supabase (any) | Supabase London | Self-hosted Postgres UK |
| Storage | Cloudflare R2 | R2 UK jurisdiction / S3 London | UK object storage |
| Telephony | Twilio | Telnyx UK, London media | Gamma/BT Wholesale SIP |
| STT | Deepgram US | Deepgram EU / self-hosted | Whisper / Parakeet on UK GPU |
| LLM | OpenAI/Anthropic | Azure OpenAI UK South, zero-retention | Llama 3 / Mistral / Qwen on UK GPU |
| TTS | Cartesia | ElevenLabs EU residency | Kokoro / Piper / Orpheus on UK GPU |
| Frontend | Vercel | Cloudflare Pages (UK) | Self-hosted |
| Analytics | GA allowed | No US trackers | No third-party scripts |

### Telephony demarcation (in SLA/T&Cs)
Parlio owns and guarantees AI numbers, carrier, SIP edge and AI pipeline; customer/provider owns business line, forwarding and PBX — "assisted, not guaranteed", diagnosed and logged by Parlio on the customer's behalf (Phases 17/18).

### Scale-ready foundations (built in from the start, sized small)
- Stateless voice workers on Kubernetes with autoscaling on active-call count + ~20% pre-warmed headroom; LiveKit in cluster mode (Redis) from day one; SIP edge deployable as 1 node -> N nodes behind DNS SRV.
- tenant_id in every table/index, calls/transcripts partitioned by month; event bus decouples live calls from post-call work; analytics served from a read model (Postgres views now, ClickHouse later — same API).
- Provider abstraction with per-vendor failover (Deepgram<->AssemblyAI, Cartesia<->ElevenLabs, OpenAI<->Azure OpenAI<->Anthropic) and per-tenant rate limits/fair-share scheduling; Enterprise priority and isolated pools are config, not code.
- Terraform + Helm for everything; growth = replica counts, node pools, carrier channel purchases, vendor tier upgrades. Capacity runbook with triggers (e.g. at 150 tenants order more SIP channels; at 300 add second LiveKit node; at 1,000 concurrent move analytics to ClickHouse).

### Environments & release engineering (from Phase 1)
- dev (per-branch preview: dashboard preview deploy + ephemeral API/DB), staging (full production replica — own LiveKit, SIP edge, DB, dedicated test numbers/SIP trunks, sandbox Stripe/Twilio/Telnyx, vendor test keys), production. Identical Terraform, different sizes/secrets.
- CI: unit + integration tests, contract tests for adapters, simulated-call suite (scripted callers through the real STT->LLM->TTS pipeline against staging numbers, asserting transcripts/bookings/transfers and latency budgets), lint/typecheck, security scan.
- Deploy: zero-downtime rolling updates with call-draining (in-flight calls finish on old workers); expand-then-contract DB migrations; blue/green SIP edge and LiveKit via DNS SRV weight shift.
- Progressive delivery: feature flags (Unleash/LaunchDarkly), canary to internal/support tenant -> 5% of tenants -> all, automatic rollback on answer-rate/latency/QA regression (Phase 17 monitors); prompt/assistant versions pinned per tenant with one-click rollback; opt-in release windows for Enterprise/Sovereign.
- Staging doubles as the demo and sales-training environment.

---

## Part A — Core platform (Dena parity + ticketing + SIP) ~10.5 sessions

### Phase 1 — Voice core + environments (2.5 sessions)
- Repo scaffold (monorepo: voice-worker, api, web, infra), Terraform/Helm for dev/staging/prod, CI/CD pipelines, feature-flag service.
- LiveKit (cluster mode) + SIP + Agents worker on Kubernetes. Number -> SIP trunk -> agent answers.
- Streaming STT->LLM->TTS, barge-in, VAD, configurable greeting/voice/persona. Provider interfaces + failover hooks from day one.
- Call recording: dual-channel via Egress -> object storage; per-tenant on/off; recording-consent announcement (per language) before greeting.
- Call lifecycle events -> event bus -> Core API. Latency harness (target p50 <500ms); first simulated-call CI test.

### Phase 2 — Core API + data model (1 session)
- FastAPI + Postgres, RLS, worker API keys. region_profile on organization drives provider selection. tenant_id everywhere, monthly partitions, analytics read model.
- Tables: organizations, companies, users/memberships, phone_numbers, sip_trunks, assistants (+ versions), calls, transcripts, recordings, transfers, tickets/ticket_events, contacts, prospects, sms_scenarios, faqs, business_rules, required_fields, blocked_numbers, notification_prefs, calendar_connections, integrations, subscriptions, audit_log, feature_flags.
- Post-call pipeline via event bus: summary, structured extraction (required fields), new/returning classification, missed-information flags.

### Phase 3 — Human transfer + ticket/callback queue (2 sessions)
- Destinations & departments (POCs, extensions, availability schedule, fallback destination). Warm transfer (AI briefs human, then bridges); cold fallback. Transfers to PBX extensions over the customer's SIP trunk when connected (SIP REFER or bridge) — internal caller ID, no PSTN cost.
- Transfer recording + transcript stored separately; outcomes (answered / no answer / voicemail / rejected / ticketed).
- Ticket queue when no human available (outside hours, no answer, busy, caller declines to wait): AI intake (name, number, reason, urgency, callback window) -> ticket with AI priority/category/department/SLA -> Slack/SMS/email alerts with escalation -> dashboard board (open/claimed/resolved, assign, notes, click-to-call, SLA countdown) -> caller SMS confirmation + reminders -> returning caller recognised.
- Hold-and-callback: auto-dial caller when human becomes free and bridge.
- Emergency/urgent-keyword detection -> immediate escalation to on-call human.
- Transfer + queue analytics (transfers by destination/department, AI vs human duration, tickets created/resolved, time-to-claim, SLA breach).

### Phase 4 — Dashboard (2 sessions)
- Auth (Supabase, Google OAuth), onboarding wizard (website analysis + Google Places -> business info/FAQs).
- Calls: list/filters (answered/missed/transfers/blocked/ticketed, day/hour), detail (Overview / AI Recording / Transfer Recording / Recording & Transcript), mark read, share summary link, feedback (incorrect response, tone issue, etc.).
- Tickets board. Contacts/Prospects (caller memory, VIP). Org members/invites. Profile & Security. How-to-launch (carrier forwarding codes, UK + US).
- Analytics: total calls, answer rate, avg duration, avg calls/caller, new callers, volume by hour/day-of-week, daily trend, period comparison (two periods), transfer & ticket metrics, prospect insights, usage this month.
- Assistant Studio: persona/voice selection, business hours/timezone, key business rules, FAQs by category (+ AI-suggested FAQs), required fields, SMS scenarios (examples/bulk), languages/bilingual, recording + consent toggle, blocked numbers, after-hours behaviour (voicemail/ticket/both); assistant version history + rollback.

### Phase 5 — Integrations & notifications (1 session)
- SMS during/after call (Twilio Messaging) via scenarios. Google/Outlook Calendar OAuth (+ Cal.com/Square/GHL booking links), mid-call availability + booking, sync logs.
- Notifications: email (Resend), SMS, Slack app; smart notification filtering (qualified leads only).

### Phase 5b — BYO SIP trunking (1 session)
- SIP edge (Kamailio/OpenSIPS in front of LiveKit SIP): registrar, digest auth, IP allowlists, TLS/SRTP, codec negotiation (G.711/Opus), DTMF (RFC 2833/inband), NAT handling.
- Three connection modes in a Telephony page: (1) Forward to our number (default, forwarding codes); (2) Connect your PBX — Parlio issues per-tenant SIP credentials/domain (tenant@sip.parlio.co.uk) for 3CX, FreePBX, Gamma Horizon, RingCentral, BT Cloud Voice etc.; (3) Use your SIP account — customer supplies their provider's registrar/username/password, Parlio registers as that account so their DDI rings into the AI and outbound transfers/callbacks use their trunk and caller ID.
- Credential vault (encrypted per tenant), live registration status, test-call button, per-DDI -> assistant/department routing, time-based routing (AI only out-of-hours or on no-answer), multi-DDI on one trunk, concurrent-call limits.
- Provider setup guides (Voipfone, Sipgate, Gamma, 3CX, FreePBX, RingCentral); provider quirks handling (single-registration limits, sub-accounts).
- Optional: number porting / Parlio-provided SIP trunking as an upsell to remove the forwarding dependency.

### Phase 6 — Billing, hardening (1 session)
- Stripe plans/coupons, minute metering & overage, per-tenant number provisioning.
- OpenTelemetry per-call traces + latency dashboard, per-tenant rate limits and cost metering, retention policy, PII redaction, GDPR export/delete, audit log.
- Load test (2x expected launch peak), capacity runbook with scale triggers, CI/CD hardening.

---

## Part B — Business & enterprise integrations ~3 sessions

### Phase 7 — Adapter framework + generic/SME integrations (1 session)
- Interfaces: BookingProvider, CRMProvider, TicketSink, HRProvider; per-tenant field mapping UI; credential vault; sync logs & retry; adapter contract tests.
- Generic sinks first (cover the long tail cheaply): Zapier, Make, outbound webhooks + inbound API, CSV export, Google Sheets.
- Calendars: Google Calendar (done in Phase 5), Microsoft 365/Outlook via Graph API (priority — most UK SMBs are on M365), Calendly.
- CRMs: HubSpot, Salesforce, Pipedrive, Zoho — push caller/contact, log call + summary/recording link, create lead/deal.
- Team chat: Slack app (upgrade from webhook), Microsoft Teams (alerts + transfers to Teams Phone users).
- Trades reference adapter (ServiceM8 or Jobber) as the template for future verticals.

### Phase 8 — Enterprise platforms (2 sessions)
- monday.com (GraphQL, OAuth app) — tickets <-> board items, status sync back.
- ServiceNow (Table/Incident/Case APIs, OAuth) — create Incident/Case with transcript link + urgency; bi-directional state; optional Scoped App/Store later.
- Workday (HR/Absence/Recruiting via Integration System User) — HR-line use cases (leave balance, log absence, identity verification); requires customer tenant or partner access.
- Helpdesk/ticketing: Zendesk, Freshdesk, Jira Service Management — ticket create + status sync back (same TicketSink interface as monday.com).

### Phase 8b — Vertical & marketing adapters (by customer demand, ~1 session each batch)
- Trades/field service: folded into Phase 22c (SchedulingBackend). ServiceM8 + generic HMAC webhook done there; Jobber, simPRO, Commusoft, BigChange, Joblogic, Tradify remain by-demand adapters on that contract.
- Legal: Clio, LEAP. Property/lettings: Reapit, Alto. Hospitality: OpenTable, ResDiary.
- Accounting (lightweight, invoice/balance queries): Xero, QuickBooks.
- Healthcare/dental (Dentally, SystmOne/EMIS) only via approved partner programmes — heavy compliance; keep out of scope until sovereign profile (Phase 15) is live.
- Marketing/comms: Mailchimp, Klaviyo, Brevo — add caller to list only with explicit consent captured on the call (GDPR); Google Business Profile already used in onboarding.
- Suggested Part B order: M365/Outlook + Teams -> HubSpot + Salesforce -> Zapier/Make/webhooks -> Zendesk/monday.com -> verticals as customers ask; Mailchimp is a quick add once generic connectors exist.

---

## Part C — Differentiators ~8 sessions

### Phase 9 — Outbound & speed-to-lead (1.5)
- Compliant outbound dialer (hours, opt-out, jurisdiction rules); reminders/confirmations, no-show follow-up, review requests, ticket callbacks. Web form/webhook -> AI calls lead within 60s, qualifies, books or tickets.

### Phase 10 — Live monitoring, takeover & approvals (1.5)
- Live active-call view with transcript (Redis pub/sub -> WebSocket); listen / whisper to AI / take over mid-call via browser WebRTC. Human-in-the-loop approvals: AI drafts quote/booking/refund -> owner approves via SMS/Slack tap.

### Phase 11 — Omnichannel shared inbox (1.5)
- Unified threads per contact: calls, SMS, voicemail, WhatsApp Business, web chat widget — same assistant brain and tools. Team assignment, internal notes, canned replies, unread/SLA states.

### Phase 11b — Browser voice ("click to talk") & chat bundle (0.5)
- Voice-in-browser button on the web chat widget / hosted chat page using the existing LiveKit WebRTC stack and the same voice worker — no phone line needed; sessions land in the shared inbox as a call thread with transcript and post-call summary.
- Packaging: telephony + web chat + browser voice sold as one bundle sharing FAQs, hours, booking, tickets, handoff and analytics; per-channel usage metering (chat messages, browser-voice minutes) feeding Phase 6 billing.

### Phase 12 — Payments & identity (1)
- Stripe deposit/payment links via SMS mid-call (hosted Checkout; simulated provider offline), consent-gated, idempotent per tenant/call/customer/amount; refunds and status webhooks. Caller verification flows (DOB/postcode/reference) with salted hashes, configurable required matches / max attempts, redacted audit trail.
- Card capture is a PCI-safe provider seam only (`CardCaptureProvider`: Twilio <Pay>-style DTMF or a Telnyx equivalent) — no raw card data ever transits Parlio; activate once a live PSTN number is approved.

### Phase 13 — QA, self-improvement & simulation (1.5)
- Per-call AI QA scoring (resolution, tone, accuracy, hallucination flags); low-score alerts. Insight engine clusters unanswered questions -> suggested FAQs/rules with one-click apply. Simulation sandbox (browser/phone) against draft config, scripted test callers, prompt A/B — also used pre-release. Owner voice cloning with consent.

### Phase 14 — Business value, white-label, compliance (1)
- Lead scoring, revenue attribution (call -> booking -> value), missed-revenue report, weekly owner digest (email/WhatsApp). Tracking numbers per marketing channel. White-label/reseller mode (custom domain, branding, agency parent accounts, per-client billing). Compliance pack UI (consent, retention, redaction, audit export, region pinning).
- Account security: 2FA (TOTP authenticator app via Supabase MFA, optional recovery codes) with per-tenant enforcement for admins/all members; session/device list and revoke; enterprise SSO (SAML/OIDC — Microsoft Entra, Google Workspace, Okta) and SCIM provisioning for Enterprise/Sovereign tiers.

---

## Part D — UK Sovereign tier ~5 sessions

### Phase 15 — Region profiles & UK-region deployment (2 sessions)
- region_profile provider selection end-to-end (compute, DB, storage, telephony, STT/LLM/TTS, frontend, trackers), per-tenant enforcement, "sovereignty report" page listing every processor and location.
- Terraform for AWS London/Azure UK South; Telnyx UK numbers with London media region; Azure OpenAI UK South zero-retention; Deepgram EU; ElevenLabs EU residency.
- Hard-disable US trackers/Vercel in sovereign mode; auto-generated sub-processor list. Single-tenant/private-cloud bundle (Helm/Terraform) for NHS, legal, finance.

### Phase 16 — Self-hosted model stack (strict) (2 sessions)
- UK GPU workers: faster-whisper or NVIDIA Parakeet (STT), vLLM + Llama 3/Mistral/Qwen with tool calling (LLM), Kokoro/Orpheus (TTS); latency/quality benchmark vs vendor stack; per-tenant switch (also the long-term cost/quota lever at scale).
- Compliance collateral: UK GDPR DPA with no international transfers, ICO registration, Cyber Essentials Plus -> ISO 27001 path, pen test, G-Cloud listing prep, DPIA template for customers.

### Phase 16b — Platform owner console: analytics, subscriptions & tenant management (1)
- Superadmin role (platform staff only, separate from tenant roles; 2FA enforced) and an `/admin` area of the dashboard, cross-tenant data never exposed to tenants; every admin action written to the audit log.
- Subscription management: create/edit plans (price, included minutes, seats, features, overage rates), coupons/credits; per tenant: change plan, extend/convert trial, apply credit or discount, pause/suspend/reactivate, cancel, issue refund, view invoices and payment status (Stripe-backed with simulated fallback), override usage caps and rate limits.
- Tenant management: tenant directory with search/filters, tenant detail (config, numbers, trunks, integrations, members), feature flags per tenant, support login ("view as tenant", read-only by default, logged), notes, resend invites/reset 2FA, GDPR export/erase on request, force number release.
- Platform staff: invite/remove staff, roles (owner / support / finance / read-only), IP allow-list.
- Business analytics: tenants/sign-ups/trials/conversions/churn over time, plan mix, MRR/ARR and overage revenue, top accounts, cohort retention.
- Demand analytics: calls and minutes per day/hour across all tenants, peak concurrency vs capacity, growth trends and forecasts, inbound vs outbound, channel mix (voice/SMS/WhatsApp/web chat), missed/failed rate, transfer and ticket volumes.
- Quality & cost: answer latency and turn latency p50/p95, STT/LLM/TTS vendor spend per minute and gross margin per tenant, QA score trends, integration/connector usage and failure rates.
- Drill-down to a tenant health page (feeds Phase 17's health engine), CSV export, platform status/incident banner.

---

## Part E — Support & operations tooling ~3.5 sessions

### Phase 17 — Proactive monitoring, reliability & telephony fault detection (2 sessions)
- Tenant health engine: answer rate, latency p95, dropped/failed calls, low QA scores, integration sync failures -> ops alerts before customers notice; per-tenant health score in internal admin console; metrics also gate canary rollouts.
- Synthetic test calls: daily and post-deploy/post-config-change scripted calls to every tenant's AI number verifying greeting, FAQ, booking and transfer paths; failures open internal tickets.
- Forwarding health: baseline each tenant's call pattern; zero inbound during expected hours -> "forwarding may be off" alert with per-carrier re-enable guide; synthetic call to the customer's business number verifying it routes to the AI.
- SIP trunk health: registration state, OPTIONS pings, failed INVITE/auth rates, per-call audio quality (MOS, jitter, packet loss, one-way audio), codec/DTMF mismatches; alerts and auto-remediation (re-register, failover to forwarding).
- Fault classification engine: attributes each failure to Parlio side / carrier / customer PBX-provider using CDRs, SIP traces and quality metrics; plain-English explanation + next steps + evidence pack (timestamps, call IDs, SIP trace excerpts, test results).
- Public status page (per-region components: voice UK/US, SMS, WhatsApp, SIP edge, integrations) auto-updated from monitors; on-call rota (PagerDuty/Opsgenie) for P1; automated carrier (Twilio <-> Telnyx) and region failover; runbooks; RCA template (48h for enterprise/sovereign).
- SLA tiers encoded: 99.9% voice uptime (99.95% Enterprise/Sovereign) covering platform components only; forwarding/customer SIP "assisted"; P1 15 min 24x7, P2 1h business hours, P3 next day; service-credit calculation.

### Phase 18 — AI-powered support desk incl. telephony fault assist (1 session)
- Support tenant on Parlio itself: support line, in-app chat, WhatsApp, email — all answered by Parlio's own agent 24/7 (dogfooding + live demo).
- Agent tools: docs/KB search; get_tenant_status (recent calls, latency, integrations, billing, SIP registration, forwarding health) with identity verification; walkthrough_forwarding_setup / walkthrough_sip_setup per carrier/PBX; run_synthetic_call (to AI number or customer business number); run_sip_diagnostics; create_support_ticket; warm transfer / P1 escalation to on-call human.
- Telephony fault logging on the customer's behalf: for customer/provider-side faults, agent opens a fault ticket with the evidence pack, generates a provider-ready fault report, optionally emails the customer's provider support (with consent), tracks in the queue until forwarding/SIP is verified healthy; offers porting/Parlio SIP as the permanent fix.
- Human support business hours (8am-8pm UK Mon-Sat) via ticket queue and shared inbox; Slack Connect + named CSM for Enterprise/Sovereign; 24/7 human response only for paid P1 SLAs.
- Helpdesk with SLA timers, escalation to engineering (Linear), CSAT after every ticket, weekly ticket-tag review into product backlog.

### Phase 19 — Onboarding, self-serve & trust (0.5 session) — PR #24
- Guided sign-up journey (Dena-style): monthly call volume → what the assistant should do → business type/languages/team/channels/UK-sovereign → website/Google lookup → confirm details → recommended plan (smallest plan whose entitlements + minutes cover the answers, estimated cost incl. overage, Enterprise = talk to sales) → FAQs → assistant → tenant created on that plan's trial → `/setup` checklist + optional checkout.
- In-app setup checklist (`/setup`: assistant, number, test call, alerts, calendar, team, billing; live = real answered calls), one-click test call (runs the Phase 17 synthetic caller; browser click-to-talk link when the widget has voice on), "Why did it say that?" tab on call detail (assistant turns matched to the FAQ/rule/business fact/hours/greeting of the config version live at call time — heuristic wording match, not model provenance), vertical playbooks (trades, salon, hospitality, professional, dental, legal, property, general) applied at onboarding.
- 7-/30-day check-in automation (`CheckInLoop` → owner digest notification with checklist progress, idempotent per tenant/day).
- Phase 19b leftovers (`adoption.py`): white-glove onboarding requests for plans with `priority_support` (tenant books from `/setup`, staff queue under Platform admin → Announcements with schedule/assign/notes visible to the customer); first-week impact report (`GET /v1/setup/first-week`, card on `/setup`, embedded in the day-7 check-in email); guided FAQ import in Studio (paste text / URL / CSV → parsed + de-duplicated against existing FAQs → review/edit → apply saves a new assistant version); announcements (staff-authored, draft/published/pinned, per-user read state, tenant `/whats-new` feed, public `/changelog`), public roadmap with one vote per tenant per item (`/roadmap`), in-app feedback with staff triage. Grouped sidebar (Conversations / Assistant / Insights / Account).
- Still to do: docs site (forwarding + SIP guides UK/US, integrations, API).
- Trust centre (`/trust`, `GET /v1/public/trust`): data residency, DPA, recording/consent, security, incident response & breach notification, telephony demarcation, data-subject rights, certifications (Cyber Essentials in progress; ISO 27001/SOC 2 roadmap — wording needs legal review before launch), sub-processor table.

---

## Part F — Integration test, tuning, polish ~3 sessions
- E2E scenario tests (real numbers, forwarding + both SIP modes, all channels, both region profiles), latency tuning per region, load test, mobile polish, docs/runbooks.
- Call-flow parity features built ahead of F so the live-call pass covers them (see Phase 20, items 20a–20d — **done**): owner post-call SMS summary, SMS appointment reminders with reply 1/2 confirm/reschedule (+ STOP/START opt-out), call screening & spam filtering, Microsoft 365/Outlook calendar alongside Google. Regression pack `apps/api/tests/test_callflow.py`.
- **Status — automated part done** (`docs/runbooks/e2e-and-load.md`): journey-level E2E suite `apps/api/tests/test_e2e_scenarios.py` (forwarding, PBX and BYO SIP modes, warm transfer + human-leg recording, tickets/callbacks, AI call back with resolution, booking + reminders, screening, SMS/WhatsApp/web chat, GB + US profiles, GDPR erase, all Phase 20 flows) runs in CI; deployed runner `tools/e2e` (`parlio-e2e`) passes 13/13 against the droplet at concurrency 10; load test at 30 and 60 concurrent recorded; endpointing tuned (`max_endpointing_delay` 2.0 → 1.2 s) from the measured EOU share of turn latency; dashboard usable at 390 px (no document overflow; wide tables/tabs scroll internally). **Still human-run**: real forwarding, PBX/BYO handset, owner SMS, reminder texts and replies, warm transfer to a real person — script in `docs/runbooks/live-call-test.md`; US media tuning waits for a US-hosted worker/number.
- F test scenarios to add for those: owner receives one summary text per call and none when the rule is paused; a booking made on a live call produces the reminder text at the configured offset, "1" confirms, "2" raises a reschedule ticket and stops further reminders, STOP suppresses all texts; withheld/spam/blocked callers are rejected before the assistant answers, unknown callers are screened then helped, allow-listed and known contacts go straight through, and screened/blocked outcomes show on Calls; Google and Microsoft calendars both take a booking from a live call (free/busy honoured, event created).

### Phase 20 — Competitive parity (Dena / RingCentral AI Receptionist) ~2.5 sessions
20a–20d touch the call flow and were pulled forward ahead of Part F (done). 20f, 20g, 20i are done (one PR after Part F). **20e, 20h and 20j are deferred until the customer base justifies them** — not scheduled before Part F or Part H.
- **20a Post-call SMS summary to the owner (0.25) — done** — `call.completed` notification rule on the SMS channel sends one concise text per call (caller name/number, reason, outcome, callback number/address; "URGENT" prefix when escalated) via the Telnyx messaging profile; owner/admin enable, pause, resume and test it from Integrations → Notifications ("Text me after every call"); metered as SMS.
- **20b Two-way SMS appointment confirmations (0.5) — done** — `reminders.py`: per-tenant policy (enabled, offsets e.g. 24h/2h, tenant timezone, templates), a reminder per booking per offset, sweep loop, never sent after the appointment or for cancelled bookings. Replies via the Inbox SMS webhook: 1/YES confirms the booking, 2/CHANGE marks it reschedule-requested, cancels the remaining reminders and raises a callback ticket; anything else falls through to the Inbox AI. STOP/START opt-out honoured by every SMS send. Voice reminders (Phase 9) unchanged.
- **20c Microsoft 365 / Outlook calendar (0.5) — done** — `MicrosoftCalendarBackend` (Graph OAuth, token refresh, calendarView free/busy, event creation) alongside Google in `calendar.py`; Integrations → Calendar picks provider per assistant. Needs `PARLIO_MICROSOFT_CLIENT_ID/SECRET` on the VPS. Mocked regression tests for both providers.
- **20d Call screening & spam filtering (0.5) — done** — `screening.py`: per-assistant mode (off / unknown callers / everyone), block withheld, block spam, allow list; tenant and platform spam lists (Studio + Platform admin); repeat-short-call robocaller heuristic. Worker asks the API before answering: reject ends the call before the assistant starts (no minutes billed, shown as "Blocked" on Calls), screen makes the assistant ask who is calling and why and only hang up on sales/robocall/silent/refuses-to-identify. Known contacts and allow-listed numbers always pass.
- **20e Mobile: PWA + push notifications (0.5) — deferred until customer demand** — installable dashboard (manifest, service worker, offline shell), Web Push for new ticket / missed call / chat waiting / live-call alerts, deep links to the ticket or call; native wrapper later if needed. Revisit once there is a customer base asking for it; the responsive web dashboard covers mobile until then.
- **20f Business-hours vs after-hours personas (0.25) — done** — `AfterHoursPersona` on the assistant (own greeting, optional holiday greeting, tone, extra instructions, intake-only, transfer policy normal / on-call only / never, quote next opening) plus `Schedule.holidays` (closed all day or reduced hours, tenant timezone). The worker picks the open / closed / holiday window at pick-up, uses that window's greeting and prompt, and the availability + transfer tools enforce the after-hours transfer policy; Studio → After hours configures it. Assistants without the section behave exactly as before.
- **20g Multi-location / multi-brand routing (0.25) — done** — sites (id, name, brand name, address, timezone, numbers) on the assistant, exposed as `/v1/sites` and `PUT /v1/assistants/{id}/sites` (unique ids and numbers). Inbound dialled number → site: the assistant answers as that site's brand, knows its address, and `Destination.site_id` makes transfers prefer that site's people (shared destinations still used). Calls and Analytics take `?site=`, `/v1/analytics/sites` gives the franchise roll-up; dashboard Locations page + Location picker on Transfers destinations.
- **20h Full payments over the phone / payments IVR (0.5) — deferred until customer demand** — PCI-DSS SAQ-A-EP card capture via a DTMF/IVR-masking provider (e.g. PCI Pal / Stripe Terminal-style agent-assisted) on top of the Phase 12 payment seam; recording pauses during capture.
- **20i Transcript & recording search (0.25) — done** — `GET /v1/calls/search?q=` over summaries + transcripts: memory store substring match locally, Postgres `websearch_to_tsquery` with GIN indexes (migration 0008) in production; ClickHouse remains the Part G extension point. Hits carry the matching transcript moments (role, snippet, time offset, recording index) and the Calls page search box links each moment to the call's recording, which seeks to that moment on open. Tenant-scoped, site filter supported.
- **20j Full PBX replacement / human-answered call inbox (deferred — much later, once the platform is established)** — RingCentral is a full phone system; Parlio stays "in front of your phones" (forwarding or BYO SIP trunk). Softphones, desk-phone extensions, hunt groups, voicemail boxes and a phone-system replacement are explicitly out of scope for now. The only slice worth picking up earlier, on demand: voicemail-to-text for unanswered transfers and a shared inbox entry per human-answered call (using the transfer recordings of the human leg).

### Phase 21 — Deep analytics & AI business advisor ~3 sessions
Turns the data Parlio already collects (calls, transcripts, tickets, transfers, bookings, leads, QA, recordings) into operational insight and, on top of that, recommendations. All aggregates live in `analytics.py` / `value.py` read models (Postgres views first; ClickHouse when Part G triggers); the advisor only ever sees aggregated metrics, never raw PII.

**21a Analytics read models (1 session) — DONE** (`insights.py`, `GET /v1/analytics/insights`; computed in-process from the tenant-scoped store — roll-up tables/ClickHouse deferred to Part G).
- **Demand & staffing** — volume heat-map (hour × weekday), 7-day forecast (seasonal naive → Prophet-style later), "would have been missed without the assistant" by hour, concurrency peaks.
- **Resolution funnel** — answered → resolved by AI / transferred / ticketed / callback → resolved by human; time at each step, first-contact resolution %, where work leaks to humans.
- **Ticket & callback SLA** — time to claim, time to first human callback, % callbacks resolved on the first AI attempt, reopen rate, backlog age by department, SLA-breach trend.
- **Transfer quality** — answer rate and pick-up time per person/department, human talk time (Phase transfer-recording), abandoned-while-ringing, cost of a transfer vs AI handling.
- **Intent & FAQ gap** — top intents, week-on-week trending questions, unanswered-question clusters (Phase 13 insights) weighted by revenue.
- **Revenue & pipeline** — conversion by intent/source/hour, average job value per intent, lead-to-booking time, value lost per missed/unresolved call, repeat-caller share and lifetime value per contact.
- **Customer experience** — sentiment/frustration per call and trend, repeat calls about the same issue within 7 days (friction signal), QA score by intent and by assistant version (before/after each Studio publish).
- **Workforce** — per member: tickets claimed, resolution time, callbacks made, transfers answered/missed, hours covered; vs team average. Owner/admin only.
- **Marketing attribution** — tracking numbers per channel → calls, leads, bookings, value (extends Phase 14).
- **Cost & efficiency** — minutes handled by AI vs human, cost per resolved enquiry, hours saved; margin view for platform admin.
- **Trends** — long-range views over any window (month, quarter, last 12 months, all time): average call volume by hour-of-day and by weekday for the period (e.g. "Mondays 8–9am averaged 14 calls over the past year"), month-on-month and year-on-year series for calls, missed, resolution rate, transfers, tickets, bookings and value; seasonal patterns (busiest months/weeks, holiday dips), busiest-days/hours leaderboard, and per-intent trend lines so a business can see demand shifting (e.g. boiler servicing rising into autumn). Backed by monthly/hourly roll-up tables so a year of history renders instantly, with a same-period-last-year comparison toggle.

**21b Analytics UI (0.75) — DONE** — "Insights" section on Analytics (Demand, Trends, Resolution, Team, Customers, Revenue tabs) with premium chart primitives (`app/charts.tsx`: smooth shaded area line, donut, heat-map, funnel, columns, ranked bars, metric tiles) also applied to the existing Call volume / Calls by department cards; usage card now covers calls, minutes, transfers, tickets, bookings, SMS, WhatsApp, web chats and emails. CSV export of every section (`reports.py`, `/v1/analytics/insights.csv`, per-tab Export button) and scheduled weekly/monthly report emails via notification rules (`analytics.report` event, Reports & export tab). Deferred: "Ask AI" over the new measures.

**21c AI business advisor (1 session) — DONE** (`advisor.py`, Analytics → Advisor tab, `advisor.digest` event; LLM only rewords aggregate evidence and is capped per tenant/month) — `advisor.py`: weekly (and on-demand) run that reads the 21a aggregates for the period, detects notable patterns with deterministic rules first (spikes, unanswered hot-spots, SLA drift, cold leads, low-QA segments, FAQ gaps) then asks the LLM to write prioritised recommendations, each with: evidence (the numbers and a link to the filtered view), expected impact (£ / hours / calls), confidence, and a concrete action. Actions are one-click where Parlio owns the lever (draft FAQ/rule into Studio as a proposal, enable AI callbacks for an intent, extend a department's hours, add a member to a department, change the calling window, suggested rota coverage by hour) and advisory otherwise (staffing, pricing, coaching from transfer recordings). "Advisor" page + weekly digest (email/Slack/Teams) with Apply / Dismiss / Snooze; every applied recommendation is tracked so the next report shows whether the metric moved. Plan-gated (Growth+), audit-logged, tenant-isolated, no PII in prompts.

**21e Contact intelligence (0.5) — DONE** (`contacts.py`, Contacts → "How callers are classified", worker `/caller-context`) — today Prospect / Customer / VIP are manual labels and the assistant only knows "new vs returning". Add: automatic promotion Prospect → Customer on a completed booking, paid payment, resolved job ticket or CRM status sync; VIP by tenant rule (lifetime value threshold, repeat-caller count, named accounts) or manual; users can always set or change Prospect / Customer / VIP / Blocked by hand on the contact (and from the call/ticket view), and a manual choice pins the status so automatic rules never override it (with an audit note of who changed it); contact context passed to the assistant at call start (name, status, VIP, last-call summary, open tickets/bookings) so it greets returning callers by name, never re-asks known details and follows tenant-defined VIP rules (e.g. always offer a transfer, priority ticket, dedicated department); status/VIP filters and lifetime value on Contacts and in the 21a revenue/CX measures.

**21d Tests & guardrails (0.25) — DONE** (`tests/test_phase21.py`) — golden fixtures for each measure, advisor regression set (fixed metrics → expected recommendation classes), cost cap per run, tone/claims check (no invented numbers: every figure in a recommendation must appear in the evidence payload).

### Phase 22 — Team scheduling, schedule view & scheduling-tool integration ~2.5 sessions — done (Team → Engineers & scheduling, Schedule page; ServiceM8 + generic webhook scheduler; ServiceM8 shifts assume Mon–Fri 08:00–17:00 until a rota endpoint is added)
Today booking is one calendar per tenant (the primary `CalendarConnection`, with booking rules and service types from PR #61 and full job details on the event from PR #63). A business with a team of field staff — e.g. four engineers rostered on a shift, each to be booked out for a full day — cannot use that: every booking lands in one diary. Phase 22 adds a resource layer above the calendar backends, and lets a company's own scheduling/job-management tool be the booking system of record instead of a calendar.

**22a Engineers as bookable resources (0.75) — done** — `resources.py`: a `Resource` per engineer/technician/room (name, skills = which service types they can do, site, working hours or per-day shifts in the tenant timezone, active flag, on-call flag) and a calendar per resource — either that person's own Google/Outlook account connected individually, or one Workspace/365 connection with several calendar IDs (one per resource) chosen from the account's calendar list. Rota: hours on the resource for now; later read from a shared "rota" calendar (shift events → working windows) and from the scheduling tool (22c). Integrations → Calendar gets a "Team" table (add/edit/deactivate; assign calendar; skills; hours). Single-resource tenants are unchanged: the primary connection is implicitly one resource.

**22b Pooled availability & assignment (0.5) — done** — `check_calendar` offers a slot when *any* active resource that can do the requested service is free at that time on that day's shift (booking rules — grid, finish-by-close, travel gap, notice, horizon — applied per resource against that resource's busy periods). The caller never picks an engineer, so the team's whole capacity is offered and every engineer fills to a full day. On `book_appointment` Parlio picks the resource by a tenant-set policy: least-loaded that day (default), round robin, nearest by postcode area (outward code match on the resource's areas), or preferred resource for a returning customer (from the contact record, set on completed jobs). The event is written to the assigned resource's calendar with the PR #63 job details; the Bookings table, owner SMS summary, reminder texts and booking connectors carry the assignee; owner can reassign in the dashboard (moves the event; conflict-checked). Emergencies book the on-call resource under the existing emergency exception. Guide + Ask Parlio page.

**22c Scheduling-tool integration (0.75) — done** — many trades/field-service businesses run a scheduling or job-management tool that already knows the rota, skills, travel areas and job durations; booking must go there, not to a calendar. Add a `SchedulingBackend` protocol next to `CalendarBackend` (list resources + shifts, availability for service/area/date range, create job/appointment with customer + address + notes, update/cancel, webhook for changes) and make the booking flow provider-agnostic: a tenant's booking system is *either* calendar(s) + Parlio resources (22a/b) *or* a scheduling tool. First adapters by UK demand: ServiceM8 (extend the Phase 7 reference connector), Jobber, simPRO, Commusoft, BigChange, Joblogic; generic fallback = webhook-based "external availability" (tenant's own API answers `GET availability` / `POST booking` with a documented contract, HMAC-signed) for in-house systems. Job details, assignee and status sync back into Bookings; reminders and the owner summary work identically. Adapter contract tests in the Phase 7 harness.

**22d Schedule view (0.5) — done** — when bookings go into individual Google/Outlook calendars the office user has no single picture of the team's day, so add a "Schedule" page in the dashboard: day and week views with one lane per engineer/resource (or one lane for single-calendar tenants), each booking as a block showing service, customer, postcode area and status (confirmed / reminder sent / confirmed by SMS / reschedule requested / cancelled), the resource's shift shaded and off-shift greyed, travel gaps visible, and existing non-Parlio calendar events shown as busy blocks so free capacity is obvious. Click a block → booking details (PR #63 fields, link to the call, reassign / reschedule / cancel, which moves or removes the calendar event); filter by site, service and resource; today's utilisation per lane (booked hours / shift hours); jump to a date. Read from the resource calendars via the existing backends (free/busy + Parlio's own bookings), cached briefly, with a refresh button — Parlio never becomes the source of truth for events it didn't create. When a scheduling tool (22c) is the system of record the same page renders that tool's schedule read-only. Mobile-friendly (day view). Guide + Ask Parlio page.

**22e Plan caps & entitlements for Phase 20-22 features — done** — every plan (Platform admin → Plans, per-tenant overrides on the tenant page) now carries caps for active engineers / bookable calendars (Starter 1 / Growth 5 / Scale 25 / Enterprise unlimited), locations (1 / 3 / 10 / unlimited) and dashboard users (2 / 5 / 15 / unlimited; 0 = unlimited), plus entitlements `team_scheduling`, `multi_location`, `after_hours_personas`, `transcript_search` (Growth+) and `scheduling_tool` (Scale+). Enforced server-side at the add points with an upgrade message; tenants over a lowered cap keep what they have but cannot add more; Billing shows used / cap.

---

## Part G — Enterprise-scale step-up (deferred; ~1-2 sessions when triggered)
- Trigger: ~200 tenants or 300+ concurrent calls. Actions (config/infra only): multi-node SIP edge + RTPengine, additional LiveKit nodes, larger worker pools per region, Postgres read replicas + pgBouncer, ClickHouse for analytics read model, NATS/Kafka in place of Redis Streams, enterprise vendor tiers + full multi-vendor failover, active-active UK+US, isolated pools for Enterprise/Sovereign, chaos testing.
- Reference capacity: 1,000 concurrent calls ~ 60-100 vCPU workers + 2-3 LiveKit nodes + SIP edge pair ~ GBP 1.5-3k/mo infra; ~1.5M minutes/mo; ~3,000 SME tenants.

## Part H — Go-live checklist (after Part F; mostly configuration and business setup, ~1 session of engineering)
**Marketing site (`apps/marketing`) — built**
- Static Next.js export on Cloudflare Pages project `parliotec-marketing` (CI job `marketing`: main → production, branches → preview). Pages: home, platform, compare, industries, how-it-works, pricing (live from `GET /v1/public/site`, platform-owner costs excluded), demo ("Hear it live" browser call to the demo tenant, IP + monthly-minute caps, 3-min client cap), contact (`POST /v1/public/site/contact`), trust; legal drafts (terms, privacy, cookies, AUP, DPA, call-recording notice) with `[placeholders]` for company details.
- To launch: attach `parliotec.com` + `www` to the Pages project (done; `parliotec.co.uk` redirects to it), set repo vars `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_DASHBOARD_URL` / `NEXT_PUBLIC_SITE_URL`, set `PARLIO_SITE_URL` on the API for CORS, fill legal placeholders after solicitor review, confirm `hello@`/`privacy@`/`legal@` mailboxes, check `PARLIO_PLATFORM_OWNER_EMAILS` (contact-form notifications go there) and optionally `PARLIO_SITE_DEMO_PHONE`; consider enforcing the demo duration cap server-side and moving the per-IP limiter to Redis before multi-instance API.

**Accounts & credentials (owner creates, engineering wires in)**
- Supabase project → `PARLIO_AUTH_MODE=supabase`, `PARLIO_PLATFORM_OWNER_EMAIL` set, owner 2FA enrolled; invite-only until happy.
- Stripe live keys + webhook secret, live prices per plan, VAT settings; one real low-value checkout end to end.
- Telnyx: approved number(s), number pool for per-tenant provisioning, SMS messaging profile + sender registration; `PARLIO_OUTBOUND_DIALER=livekit`.
- Resend domain verified (DKIM/SPF), Meta WhatsApp Business number, Google/Microsoft OAuth apps verified for calendar scopes, PagerDuty/Opsgenie for on-call.
- Custom domains: parliotec.com/www → marketing (Pages), app.parliotec.com → dashboard (Worker custom domain), api./lk.parliotec.com → droplet (Caddy TLS); parliotec.co.uk redirects to .com (Cloudflare redirect rule).

**Infrastructure hardening (engineering)**
- Managed Postgres (DO London) with daily backups + PITR; object storage for recordings; secrets moved to a vault/env manager; staging environment; `alembic upgrade head` in CD; uptime monitoring feeding the status page; load test at target concurrency (Phase 6 script).

**Legal & compliance (owner / solicitor)**
- ICO registration, T&Cs, privacy notice, DPA + sub-processor list (Trust centre copy reviewed), recording-consent wording, Ofcom CLI rules for outbound, Cyber Essentials application, PCI SAQ-A confirmation for hosted payment links.

**Operational**
- Support inbox + on-call rota, runbooks reviewed, pricing/entitlements finalised in Platform admin → Plans, onboarding email templates, soft launch with 2–3 pilot tenants before opening self-serve sign-up.

---

## Totals & build cost
- ~34 sessions (Part G excluded; includes Phase 22), ~700-820 ACUs -> ~$1,400-1,650 (~GBP 1,050-1,250) Devin cost at standard rates; upper bound ~$1,850 with heavy telephony debugging. Phase 1 is the calibration point for re-estimating.
- Third-party build-time spend ~$200-400 (staging adds a second set of numbers/trunks and a small cluster) + UK GPU hours for Phase 16; vendor programme fees (ServiceNow Store, Workday partner) if pursued.
- Calendar time ~5-7 weeks, paced by reviews and account provisioning.

## Running cost
- Per-minute variable (standard profile): telephony ~$0.012, STT ~$0.008, LLM ~$0.005, TTS ~$0.02, storage ~$0.002 => ~$0.045-0.05/min.
- 10 SMEs (~150 calls x 3 min each = 4,500 min/mo): ~$225 variable + ~$160-200 fixed (workers, Supabase, Redis, hosting, numbers) => ~$400-450/mo (~$40-45 each); ~$28 each at 50 tenants.
- At launch (<=200 tenants): staging + prod Kubernetes ~GBP 250-400/mo, ops tooling ~GBP 100-200/mo, SIP edge ~GBP 20-40/mo, plus ~GBP 35-45/tenant/mo variable; sovereign L1 ~+30%; strict L2 shared UK GPU ~GBP 300-600/mo (viable from ~10+ sovereign tenants).

## Support model
- Platform availability 24/7 (monitoring + on-call); human support business hours; AI first-line support 24/7 on all tiers; 24/7 human P1 response as paid Enterprise/Sovereign SLA.
- Telephony: Parlio faults owned and guaranteed; customer forwarding/PBX faults diagnosed and logged by the AI on the customer's behalf.
- Team to ~50 customers: founder + one part-time support/success person + Devin for engineering fixes and releases.

## Suggested pricing
- Starter GBP 79/mo — 1 number, 350 min, 75 SMS, recordings, analytics, SMS scenarios; overage 12p/min, 6p/SMS
- Growth GBP 199/mo — 3 numbers, 1,500 min, 400 SMS, human transfer, calendar booking, Ask AI, connectors; overage 10p/min, 5p/SMS
- Scale GBP 499/mo — 10 numbers, 4,000 min, 1,000 SMS, outbound, BYO SIP, simulation, white-label; overage 8p/min, 5p/SMS
- SMS pools are deliberately tight (texts are the unbounded cost); sell extra texts as an add-on bundle rather than raising pools
- Enterprise (ServiceNow/Workday, white-label, 24/7 P1 SLA, release windows) from GBP 500+/mo platform fee + 14p/min, 5p/SMS
- UK Sovereign add-on +50-100% or from GBP 499/mo; strict/private-cloud priced per deployment
- Target ~75% gross margin at typical use (~55% of allowance), >=60% for a maxed-out tenant; vendor cost ~3.5p/talk-minute (Cartesia) and ~3.5p/SMS.

## UK market context (Sept 2026)
- Cheap AI-only SME tier (GBP 29-99/mo): Ringmere, Phena, Jodie, Bridge Voice, Cyberstaff, AIAnswerPhone, The VoIP Shop, Team-Connect — mostly wrappers on Vapi/Retell/Synthflow.
- Heritage human/hybrid (GBP 160-500+/mo): Moneypenny AI, Smith.ai, Abby Connect, Electronic Receptionist.
- Platforms/agencies: Synthflow, Phoenix Respond, Eldris. US entrants: Dena, Goodcall, Rosie, Slang.ai. Telcos (Gamma, BT) starting to bundle.
- Positioning: don't compete on price at the bottom; target GBP 149-499 band and regulated/multi-site verticals with sovereign stack + enterprise integrations + transfer/ticket queue/live takeover + Dena-grade analytics.

## You provide
Twilio/Telnyx, Deepgram, OpenAI/Anthropic (Anthropic key already stored in Devin secrets), Azure UK South, Cartesia/ElevenLabs, Supabase, Stripe, cloud account for Kubernetes (AWS London recommended), Cloudflare (tokens stored), WhatsApp Business (Meta), Resend, Slack app, status page + PagerDuty accounts; test PBX/SIP account for Phase 5b; sandbox/partner access for ServiceNow PDI, Workday, monday; UK legal entity + ICO registration.

## Delivery order
Phase 1 (latency proof, environments, ACU calibration) -> Part A (5b after 5) -> Phases 17-18 before first paying customers -> Phase 15 early if first customers are UK-regulated -> Phase 22 (team scheduling / scheduling tools) when the first multi-engineer tenant onboards -> B/C by customer demand -> Phase 16 when sovereign demand justifies GPU cost -> Part G at scale trigger.
