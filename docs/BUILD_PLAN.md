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
- Trades/field service: ServiceM8, Jobber, simPRO, Tradify.
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

### Phase 12 — Payments & identity (1)
- Stripe deposit/payment links via SMS mid-call; PCI-compliant DTMF card capture (Twilio <Pay>). Caller verification flows (DOB/postcode/reference).

### Phase 13 — QA, self-improvement & simulation (1.5)
- Per-call AI QA scoring (resolution, tone, accuracy, hallucination flags); low-score alerts. Insight engine clusters unanswered questions -> suggested FAQs/rules with one-click apply. Simulation sandbox (browser/phone) against draft config, scripted test callers, prompt A/B — also used pre-release. Owner voice cloning with consent.

### Phase 14 — Business value, white-label, compliance (1)
- Lead scoring, revenue attribution (call -> booking -> value), missed-revenue report, weekly owner digest (email/WhatsApp). Tracking numbers per marketing channel. White-label/reseller mode (custom domain, branding, agency parent accounts, per-client billing). Compliance pack UI (consent, retention, redaction, audit export, region pinning).

---

## Part D — UK Sovereign tier ~4 sessions

### Phase 15 — Region profiles & UK-region deployment (2 sessions)
- region_profile provider selection end-to-end (compute, DB, storage, telephony, STT/LLM/TTS, frontend, trackers), per-tenant enforcement, "sovereignty report" page listing every processor and location.
- Terraform for AWS London/Azure UK South; Telnyx UK numbers with London media region; Azure OpenAI UK South zero-retention; Deepgram EU; ElevenLabs EU residency.
- Hard-disable US trackers/Vercel in sovereign mode; auto-generated sub-processor list. Single-tenant/private-cloud bundle (Helm/Terraform) for NHS, legal, finance.

### Phase 16 — Self-hosted model stack (strict) (2 sessions)
- UK GPU workers: faster-whisper or NVIDIA Parakeet (STT), vLLM + Llama 3/Mistral/Qwen with tool calling (LLM), Kokoro/Orpheus (TTS); latency/quality benchmark vs vendor stack; per-tenant switch (also the long-term cost/quota lever at scale).
- Compliance collateral: UK GDPR DPA with no international transfers, ICO registration, Cyber Essentials Plus -> ISO 27001 path, pen test, G-Cloud listing prep, DPIA template for customers.

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

### Phase 19 — Onboarding, self-serve & trust (0.5 session)
- In-app setup checklist, one-click test call, "why did the AI say this?" explainability view (transcript + retrieved FAQ/rule), guided FAQ import, vertical playbooks (trades, salons, hospitality, professional services).
- White-glove onboarding for Growth+ (config review, forwarding/SIP setup, test calls) with 7-/30-day check-in automation and "first week" impact digest.
- Docs site (forwarding + SIP guides UK/US, integrations, API), change log, in-app announcements, public roadmap/feedback board.
- Trust centre publishing certifications, DPA, sub-processors, data residency, recording-consent guidance, incident response and breach notification policy, telephony demarcation.

---

## Part F — Integration test, tuning, polish ~3 sessions
- E2E scenario tests (real numbers, forwarding + both SIP modes, all channels, both region profiles), latency tuning per region, load test, mobile polish, docs/runbooks.

## Part G — Enterprise-scale step-up (deferred; ~1-2 sessions when triggered)
- Trigger: ~200 tenants or 300+ concurrent calls. Actions (config/infra only): multi-node SIP edge + RTPengine, additional LiveKit nodes, larger worker pools per region, Postgres read replicas + pgBouncer, ClickHouse for analytics read model, NATS/Kafka in place of Redis Streams, enterprise vendor tiers + full multi-vendor failover, active-active UK+US, isolated pools for Enterprise/Sovereign, chaos testing.
- Reference capacity: 1,000 concurrent calls ~ 60-100 vCPU workers + 2-3 LiveKit nodes + SIP edge pair ~ GBP 1.5-3k/mo infra; ~1.5M minutes/mo; ~3,000 SME tenants.

---

## Totals & build cost
- ~31.5 sessions (Part G excluded), ~700-820 ACUs -> ~$1,400-1,650 (~GBP 1,050-1,250) Devin cost at standard rates; upper bound ~$1,850 with heavy telephony debugging. Phase 1 is the calibration point for re-estimating.
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
- Starter GBP 79/mo — 1 number, 300 min, recordings, analytics, SMS scenarios
- Growth GBP 149/mo — 600 min, human transfer, ticket queue, calendar booking, Slack, BYO SIP
- Pro GBP 299/mo — 1,500 min, multi-department, bilingual, priority support
- Overage GBP 0.12-0.15/min; extra number GBP 5/mo
- Enterprise (ServiceNow/Workday, white-label, 24/7 P1 SLA, release windows) from GBP 500+/mo
- UK Sovereign add-on +50-100% or from GBP 499/mo; strict/private-cloud priced per deployment
- Target ~70-80% gross margin; e.g. 10 customers on Growth ~GBP 1,490 revenue vs ~GBP 350 cost.

## UK market context (Sept 2026)
- Cheap AI-only SME tier (GBP 29-99/mo): Ringmere, Phena, Jodie, Bridge Voice, Cyberstaff, AIAnswerPhone, The VoIP Shop, Team-Connect — mostly wrappers on Vapi/Retell/Synthflow.
- Heritage human/hybrid (GBP 160-500+/mo): Moneypenny AI, Smith.ai, Abby Connect, Electronic Receptionist.
- Platforms/agencies: Synthflow, Phoenix Respond, Eldris. US entrants: Dena, Goodcall, Rosie, Slang.ai. Telcos (Gamma, BT) starting to bundle.
- Positioning: don't compete on price at the bottom; target GBP 149-499 band and regulated/multi-site verticals with sovereign stack + enterprise integrations + transfer/ticket queue/live takeover + Dena-grade analytics.

## You provide
Twilio/Telnyx, Deepgram, OpenAI/Anthropic (Anthropic key already stored in Devin secrets), Azure UK South, Cartesia/ElevenLabs, Supabase, Stripe, cloud account for Kubernetes (AWS London recommended), Cloudflare (tokens stored), WhatsApp Business (Meta), Resend, Slack app, status page + PagerDuty accounts; test PBX/SIP account for Phase 5b; sandbox/partner access for ServiceNow PDI, Workday, monday; UK legal entity + ICO registration.

## Delivery order
Phase 1 (latency proof, environments, ACU calibration) -> Part A (5b after 5) -> Phases 17-18 before first paying customers -> Phase 15 early if first customers are UK-regulated -> B/C by customer demand -> Phase 16 when sovereign demand justifies GPU cost -> Part G at scale trigger.
