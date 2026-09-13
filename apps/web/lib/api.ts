export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
export const TOKEN_COOKIE = "parlio_token";

export type CallKind = "answered" | "missed" | "transferred" | "ticketed" | "blocked" | "active";

export type CallRecord = {
  call_id: string;
  tenant_id: string;
  assistant_id: string;
  caller: string | null;
  dialed: string | null;
  status: string;
  kind: CallKind;
  started_at: string;
  answered_at: string | null;
  ended_at: string | null;
  answer_latency_s: number | null;
  duration_s: number | null;
  latency: { turns?: number; p50_s?: number; p95_s?: number };
  transcript: { role: string; text: string; interrupted?: boolean }[];
  recordings: string[];
  end_reason: string | null;
  summary: string | null;
  extracted: Record<string, unknown>;
  missed_fields: string[];
  caller_type: string | null;
  contact_id: string | null;
  transfers: { transfer_id: string; destination: string; department: string | null; mode: string; outcome: string; at: string }[];
  ticket_ids: string[];
  escalated: boolean;
  escalation_keyword: string | null;
  read: boolean;
  feedback: { type: string; note: string | null; actor: string | null; at: string }[];
  share_token: string | null;
};

export type TicketStatus = "open" | "claimed" | "resolved" | "cancelled";
export type TicketPriority = "low" | "normal" | "high" | "urgent";

export type Ticket = {
  id: string;
  tenant_id: string;
  call_id: string | null;
  status: TicketStatus;
  priority: TicketPriority;
  category: string | null;
  department: string | null;
  caller_name: string | null;
  caller_number: string | null;
  reason: string;
  callback_window: string | null;
  source: string;
  sla_due_at: string | null;
  sla_breached: boolean;
  assigned_to: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
};

export type TicketEvent = { ticket_id: string; type: string; actor: string | null; note: string | null; at: string };
export type TicketDetail = { ticket: Ticket; events: TicketEvent[]; sla_remaining_s: number | null };

export type TransferRecord = {
  id: string;
  call_id: string;
  destination: string;
  department: string | null;
  mode: string;
  outcome: string;
  started_at: string;
  ended_at: string | null;
};

export type TransferStats = {
  total: number;
  by_outcome: Record<string, number>;
  by_department: Record<string, number>;
  by_destination: Record<string, number>;
  answer_rate: number | null;
};
export type TicketStats = {
  total: number;
  open: number;
  claimed: number;
  resolved: number;
  by_priority: Record<string, number>;
  by_category: Record<string, number>;
  sla_breached: number;
  avg_time_to_claim_s: number | null;
  avg_time_to_resolve_s: number | null;
};
export type HandoffAnalytics = { transfers: TransferStats; tickets: TicketStats };

export type PeriodSummary = {
  start: string;
  end: string;
  total_calls: number;
  answered: number;
  missed: number;
  transferred: number;
  ticketed: number;
  blocked: number;
  escalated: number;
  answer_rate: number | null;
  avg_duration_s: number | null;
  avg_answer_latency_s: number | null;
  total_minutes: number;
  unique_callers: number;
  new_callers: number;
  avg_calls_per_caller: number | null;
};

export type Segment = {
  start: string;
  end: string;
  hours: "all" | "business" | "after";
  days: "all" | "weekdays" | "weekends";
  label?: string | null;
};

export type SegmentAnalytics = {
  segment: Segment;
  summary: PeriodSummary;
  business_hours_calls: number;
  after_hours_calls: number;
  by_hour: number[];
  by_weekday: number[];
  daily: { day: string; calls: number; answered: number; missed: number }[];
  by_department: { name: string; count: number }[];
  by_outcome: { name: string; count: number }[];
  first_time_callers: number;
  returning_callers: number;
  transfers_total: number;
  transfers_answered: number;
  tickets: number;
};

export type ComparisonAnalytics = {
  timezone: string;
  question: {
    period: Segment;
    compare: Segment | null;
    interpretation: string;
    source: "rules" | "llm";
  } | null;
  current: SegmentAnalytics;
  compare: SegmentAnalytics | null;
  change: Record<string, number | null>;
};

export type OverviewAnalytics = {
  timezone: string;
  current: PeriodSummary;
  previous: PeriodSummary;
  change: Record<string, number | null>;
  by_hour: number[];
  by_weekday: number[];
  daily: { day: string; calls: number; answered: number; missed: number }[];
  top_missed_fields: { field: string; count: number }[];
  feedback_by_type: Record<string, number>;
  transfers: TransferStats;
  tickets: TicketStats;
  prospects: {
    contacts: number;
    prospects: number;
    customers: number;
    vip: number;
    returning_callers: number;
    returning_rate: number | null;
    top_callers: { e164: string; name: string; calls: number }[];
  };
  usage: { month: string; calls: number; minutes: number; tickets: number; transfers: number };
};

// -- assistant config (mirrors parlio_voice.models.AssistantConfig) ---------------------------

export type DayHours = { open: string; close: string };
export type Schedule = { timezone: string; hours: Record<string, DayHours>; always: boolean };
export type Faq = { id?: string; category: string; question: string; answer: string; enabled: boolean; source: string };
export type BusinessRule = { id?: string; name: string; instruction: string; enabled: boolean };
export type SmsScenario = { id?: string; trigger: string; name: string; template: string; enabled: boolean };
export type Persona = { tone: string; formality: string; pace: string; extra: string };
export type BusinessInfo = {
  description: string;
  website: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  services: string[];
};
export type VoiceConfig = { provider: string; voice_id: string; speed: number | null };
export type RecordingConfig = { enabled: boolean; consent_announcement: Record<string, string> };
export type Destination = {
  id: string;
  name: string;
  department: string;
  kind: "pstn" | "sip" | "extension";
  address: string;
  priority: number;
  schedule: Schedule;
  fallback_id: string | null;
  on_call: boolean;
};
export type IntakeField = { name: string; prompt: string; required: boolean };
export type TransferConfig = {
  enabled: boolean;
  mode: "warm" | "cold";
  ring_timeout_s: number;
  destinations: Destination[];
  urgent_keywords: string[];
  after_hours: "ticket" | "voicemail" | "both";
  intake: IntakeField[];
  sla_minutes: Record<string, number>;
};

export type Assistant = {
  tenant_id: string;
  company_id: string;
  assistant_id: string;
  assistant_version: number;
  name: string;
  business_name: string;
  language: string;
  languages: string[];
  business: BusinessInfo;
  hours: Schedule;
  persona: Persona;
  rules: BusinessRule[];
  faqs: Faq[];
  sms_scenarios: SmsScenario[];
  blocked_numbers: string[];
  greeting: string;
  instructions: string;
  region_profile: string;
  providers: Record<string, unknown>;
  llm_model: string | null;
  voice: VoiceConfig;
  turn: Record<string, unknown>;
  recording: RecordingConfig;
  transfer: TransferConfig;
};

export type RequiredField = { name: string; description: string; required: boolean };

export type VersionSummary = {
  version: number;
  created_at: string;
  created_by: string | null;
  note: string | null;
  name: string;
  greeting: string;
  faq_count: number;
  rule_count: number;
};

export type Contact = {
  id: string;
  tenant_id: string;
  e164: string;
  name: string | null;
  email: string | null;
  vip: boolean;
  notes: string | null;
  status: "prospect" | "customer" | "blocked";
  call_count: number;
  first_seen_at: string;
  last_seen_at: string;
};

export type Member = {
  tenant_id: string;
  user_id: string;
  email: string;
  name: string | null;
  role: "owner" | "admin" | "member" | "viewer";
  status: "active" | "invited";
  invited_at: string | null;
};

export type Me = {
  user_id: string;
  email: string;
  name: string | null;
  mode: "dev" | "supabase";
  memberships: Member[];
  auth: { mode: "dev" | "supabase"; supabase_url: string | null };
};

export type WebsiteAnalysis = {
  url: string;
  reachable: boolean;
  business_name: string | null;
  business: BusinessInfo;
  opening_hours_text: string[];
  faqs: Faq[];
  headings: string[];
  error: string | null;
};

export type SharedCall = {
  call_id: string;
  business_name: string | null;
  started_at: string;
  duration_s: number | null;
  caller: string | null;
  summary: string | null;
  extracted: Record<string, unknown>;
  transcript: { role: string; text: string }[];
};

// -- transport ---------------------------------------------------------------------------------

async function authHeaders(): Promise<Record<string, string>> {
  let token: string | undefined;
  if (typeof window === "undefined") {
    const { cookies } = await import("next/headers");
    token = (await cookies()).get(TOKEN_COOKIE)?.value;
  } else {
    token = document.cookie
      .split("; ")
      .find((c) => c.startsWith(`${TOKEN_COOKIE}=`))
      ?.slice(TOKEN_COOKIE.length + 1);
  }
  return token ? { authorization: `Bearer ${decodeURIComponent(token)}` } : {};
}

export type Message = {
  id: string;
  tenant_id: string;
  call_id: string | null;
  to: string;
  sender: string | null;
  body: string;
  trigger: string;
  status: "sent" | "failed" | "skipped";
  provider: string | null;
  error: string | null;
  created_at: string;
};

export type NotifyChannel = "email" | "sms" | "slack" | "webhook";
export type NotificationRule = {
  id: string;
  tenant_id: string;
  name: string;
  channel: NotifyChannel;
  target: string;
  events: string[];
  enabled: boolean;
  qualified_only: boolean;
  departments: string[];
  created_at: string;
};
export type Notification = {
  id: string;
  rule_id: string | null;
  channel: NotifyChannel;
  target: string;
  event: string;
  title: string;
  body: string;
  status: string;
  error: string | null;
  created_at: string;
};

export type CalendarProvider = "google" | "microsoft" | "booking_link" | "simulated";
export type CalendarConnection = {
  id: string;
  provider: CalendarProvider;
  name: string;
  status: "pending" | "connected" | "error";
  calendar_id: string;
  account_email: string | null;
  booking_url: string | null;
  booking_vendor: string | null;
  slot_minutes: number;
  buffer_minutes: number;
  has_token: boolean;
  bookable: boolean;
  created_at: string;
};
export type Booking = {
  id: string;
  connection_id: string;
  call_id: string | null;
  start: string;
  end: string;
  name: string;
  phone: string | null;
  notes: string | null;
  status: string;
  created_at: string;
};
export type SyncLogEntry = { id: string; connection_id: string; action: string; ok: boolean; detail: string | null; at: string };

export type ConnectorProvider =
  | "webhook" | "zapier" | "make" | "google_sheets" | "hubspot" | "salesforce"
  | "pipedrive" | "zoho" | "teams" | "servicem8" | "simulated";
export type ConnectorTrigger = "call.completed" | "lead.qualified" | "ticket.created" | "booking.created";
export type ProviderInfo = {
  provider: ConnectorProvider; label: string; category: string; auth: "url" | "api_key" | "oauth" | "none";
  fields: string[]; help: string; available: boolean;
};
export type Connector = {
  id: string; tenant_id: string; provider: ConnectorProvider; name: string; enabled: boolean;
  triggers: ConnectorTrigger[]; qualified_only: boolean; target_url: string | null;
  options: Record<string, string>; field_map: Record<string, string>; has_secret: boolean;
  account_label: string | null; status: string; last_sync_at: string | null; last_error: string | null;
  created_at: string;
};
export type SyncJob = {
  id: string; connector_id: string; provider: ConnectorProvider; event: ConnectorTrigger;
  status: "queued" | "sent" | "retry" | "failed" | "skipped"; attempts: number;
  next_attempt_at: string | null; external_ref: string | null; error: string | null; created_at: string;
};
export type TenantApiKey = {
  id: string; name: string; prefix: string; created_at: string; last_used_at: string | null; revoked: boolean; key?: string;
};

export type TrunkMode = "forward" | "pbx" | "byo_register";
export type DdiRoute = { id?: string; e164: string; assistant_id: string; department: string | null; when: "always" | "out_of_hours" | "no_answer"; label: string | null };
export type SipTrunk = {
  id: string;
  tenant_id: string;
  name: string;
  mode: TrunkMode;
  status: string;
  enabled: boolean;
  provider_preset: string | null;
  sip_domain: string | null;
  sip_username: string | null;
  allowed_ips: string[];
  pbx_address: string | null;
  registrar: string | null;
  username: string | null;
  auth_username: string | null;
  outbound_proxy: string | null;
  register_expires_s: number;
  transport: "udp" | "tcp" | "tls";
  codecs: string[];
  dtmf: string;
  srtp: boolean;
  ddis: DdiRoute[];
  max_concurrent_calls: number;
  lk_inbound_trunk_id: string | null;
  lk_outbound_trunk_id: string | null;
  registration: { state: string; detail: string | null; last_seen_at: string | null; expires_at: string | null };
  last_error: string | null;
  has_password: boolean;
  created_at: string;
};
export type IssuedCredentials = { sip_domain: string; username: string; password: string; transport: string; codecs: string[]; dtmf: string };
export type TrunkView = { trunk: SipTrunk; credentials: IssuedCredentials | null };
export type TestCallResult = { ok: boolean; outcome: string; detail: string | null; simulated: boolean; at: string };
export type ProviderGuide = { id: string; name: string; mode: TrunkMode; summary: string; steps: string[]; quirks: string[]; defaults: Record<string, unknown> };

export type Plan = {
  id: string; name: string; monthly_pence: number; included_minutes: number; overage_pence_per_minute: number;
  included_numbers: number; included_sms: number; sms_overage_pence: number; max_assistants: number; max_concurrent_calls: number;
  features: string[]; enterprise: boolean;
};
export type SubscriptionStatus = "trialing" | "active" | "past_due" | "cancelled";
export type Subscription = {
  tenant_id: string; plan_id: string; status: SubscriptionStatus; period_start: string; period_end: string;
  coupon: string | null; coupon_months_left: number | null; provider: string; customer_ref: string | null; subscription_ref: string | null;
};
export type Coupon = { code: string; percent_off: number | null; amount_off_pence: number | null; months: number | null; plans: string[] };
export type CallCost = { call_id: string; minutes: number; vendor_pence: number; billable_pence: number };
export type UsageSummary = {
  tenant_id: string; plan: Plan; status: SubscriptionStatus; period_start: string; period_end: string;
  calls: number; minutes_used: number; minutes_included: number; minutes_overage: number; overage_pence: number;
  sms_used: number; sms_included: number; sms_overage_pence: number; numbers_used: number; numbers_included: number;
  base_pence: number; discount_pence: number; estimated_total_pence: number; vendor_cost_pence: number; gross_margin_pct: number | null;
  per_day_minutes: Record<string, number>; top_calls: CallCost[];
};
export type CheckoutSession = { url: string; provider: string; session_ref: string };
export type TenantNumber = {
  id: string; tenant_id: string; e164: string; country: string; provider: string; provider_ref: string | null;
  assistant_id: string; label: string | null; monthly_pence: number; created_at: string;
};
export type AvailableNumber = { provider: string; e164: string; country: string; provider_ref: string | null };
export type LatencyBucket = {
  calls: number; answered: number; answer_p50_s: number | null; answer_p95_s: number | null; turn_p50_s: number | null; turn_p95_s: number | null;
  eou_avg_s: number | null; llm_ttft_avg_s: number | null; tts_ttfb_avg_s: number | null; slow_calls: number;
};
export type LatencyReport = {
  tenant_id: string | null; since: string; until: string; target_turn_s: number; overall: LatencyBucket;
  per_day: Record<string, LatencyBucket>; per_assistant: Record<string, LatencyBucket>;
};
export type AuditEntry = {
  id: string; tenant_id: string; actor: string; action: string; target: string | null; method: string | null; path: string | null;
  status: number | null; ip: string | null; meta: Record<string, unknown>; at: string;
};
export type RetentionPolicy = {
  tenant_id: string; transcript_days: number; recording_days: number; call_days: number; redact_on_write: boolean; redact_caller_number: boolean; updated_at: string;
};
export type RetentionRun = { tenant_id: string; transcripts_redacted: number; recordings_dropped: number; calls_purged: number; ran_at: string };
export type RetentionView = { policy: RetentionPolicy; last_run: RetentionRun | null };
export type SubjectExport = { tenant_id: string; subject_e164: string; generated_at: string; contact: Contact | null; calls: CallRecord[]; tickets: Ticket[]; messages: Record<string, unknown>[] };
export type ErasureResult = { tenant_id: string; subject_e164: string; calls_purged: number; tickets_anonymised: number; messages_deleted: number; contact_deleted: boolean };

export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number; error: string };

export async function request<T>(path: string, init: RequestInit = {}): Promise<ApiResult<T>> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      cache: "no-store",
      ...init,
      headers: { "content-type": "application/json", ...(await authHeaders()), ...(init.headers ?? {}) },
    });
    if (!res.ok) {
      let error = res.statusText;
      try {
        const body = (await res.json()) as { detail?: string };
        if (body.detail) error = String(body.detail);
      } catch {}
      return { ok: false, status: res.status, error };
    }
    if (res.status === 204) return { ok: true, data: undefined as T };
    return { ok: true, data: (await res.json()) as T };
  } catch (e) {
    return { ok: false, status: 0, error: e instanceof Error ? e.message : "network error" };
  }
}

async function get<T>(path: string): Promise<T | null> {
  const r = await request<T>(path);
  return r.ok ? r.data : null;
}

export async function post<T>(path: string, body?: unknown): Promise<T | null> {
  const r = await request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
  return r.ok ? r.data : null;
}

export async function patch<T>(path: string, body: unknown): Promise<T | null> {
  const r = await request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
  return r.ok ? r.data : null;
}

export async function put<T>(path: string, body: unknown): Promise<ApiResult<T>> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) });
}

export async function del(path: string): Promise<boolean> {
  return (await request<undefined>(path, { method: "DELETE" })).ok;
}

const qs = (params: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const fetchHealth = () => get<{ status: string; env: string }>("/healthz");
export const fetchMe = () => request<Me>("/v1/me");
export const fetchCalls = (params: Record<string, string | number | undefined | null> = {}) =>
  get<CallRecord[]>(`/v1/calls${qs(params)}`);
export const fetchCall = (id: string) => get<CallRecord>(`/v1/calls/${id}`);
export const fetchAssistants = (tenant_id?: string) => get<Assistant[]>(`/v1/assistants${qs({ tenant_id })}`);
export const fetchVersions = (id: string) => get<VersionSummary[]>(`/v1/assistants/${id}/versions`);
export const fetchSuggestedFaqs = (id: string) => get<Faq[]>(`/v1/assistants/${id}/faqs/suggest`);
export const fetchTickets = (status?: TicketStatus) => get<Ticket[]>(`/v1/tickets${qs({ status })}`);
export const fetchTicket = (id: string) => get<TicketDetail>(`/v1/tickets/${id}`);
export const fetchTransfers = () => get<TransferRecord[]>("/v1/transfers");
export const fetchHandoffAnalytics = () => get<HandoffAnalytics>("/v1/analytics/handoff");
export const fetchOverview = (params: { tenant_id?: string; days?: number; timezone?: string } = {}) =>
  get<OverviewAnalytics>(`/v1/analytics/overview${qs(params)}`);
export const queryAnalytics = (body: {
  question?: string;
  period?: Segment;
  compare?: Segment | null;
  tenant_id?: string;
  timezone?: string;
}) => request<ComparisonAnalytics>("/v1/analytics/query", { method: "POST", body: JSON.stringify(body) });
export const fetchContacts = (params: { tenant_id?: string; q?: string } = {}) =>
  get<Contact[]>(`/v1/contacts${qs(params)}`);
export const fetchContact = (id: string) => get<Contact>(`/v1/contacts/${id}`);
export const fetchMembers = (tenant_id: string) => get<Member[]>(`/v1/organisations/${tenant_id}/members`);
export const fetchShared = (token: string) => get<SharedCall>(`/v1/public/share/${token}`);
export const fetchMessages = (tenant_id: string) => get<Message[]>(`/v1/messages${qs({ tenant_id })}`);
export const fetchRules = (tenant_id: string) => get<NotificationRule[]>(`/v1/notifications/rules${qs({ tenant_id })}`);
export const fetchNotificationLog = (tenant_id: string) => get<Notification[]>(`/v1/notifications/log${qs({ tenant_id })}`);
export const fetchConnections = (tenant_id: string) => get<CalendarConnection[]>(`/v1/calendar/connections${qs({ tenant_id })}`);
export const fetchBookings = (tenant_id: string) => get<Booking[]>(`/v1/calendar/bookings${qs({ tenant_id })}`);
export const fetchSyncLog = (tenant_id: string) => get<SyncLogEntry[]>(`/v1/calendar/sync-log${qs({ tenant_id })}`);
export const fetchProviders = () => get<{ providers: ProviderInfo[]; payload_fields: string[] }>("/v1/connectors/providers");
export const fetchConnectors = (tenant_id: string) => get<Connector[]>(`/v1/connectors${qs({ tenant_id })}`);
export const fetchSyncJobs = (tenant_id: string) => get<SyncJob[]>(`/v1/connectors/jobs${qs({ tenant_id })}`);
export const fetchApiKeys = (tenant_id: string) => get<TenantApiKey[]>(`/v1/api-keys${qs({ tenant_id })}`);

/** Browser-only: fetch a CSV export with auth headers and trigger a file download. */
export async function downloadCsv(what: "calls" | "contacts" | "tickets", tenant_id: string): Promise<string | null> {
  const res = await fetch(`${API_URL}/v1/export/${what}.csv${qs({ tenant_id })}`, { headers: await authHeaders() });
  if (!res.ok) return res.statusText;
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: `parlio-${what}.csv` });
  a.click();
  URL.revokeObjectURL(url);
  return null;
}
export const fetchTrunks = (tenant_id: string) => get<SipTrunk[]>(`/v1/telephony/trunks${qs({ tenant_id })}`);
export const fetchGuides = () => get<ProviderGuide[]>("/v1/telephony/guides");
export const fetchPlans = () => get<Plan[]>("/v1/billing/plans");
export const fetchSubscription = (tenant_id: string) => get<Subscription>(`/v1/billing/subscription${qs({ tenant_id })}`);
export const fetchUsage = (tenant_id: string) => get<UsageSummary>(`/v1/billing/usage${qs({ tenant_id })}`);
export const fetchNumbers = (tenant_id: string) => get<TenantNumber[]>(`/v1/numbers${qs({ tenant_id })}`);
export const fetchLatency = (tenant_id: string, days = 7) => get<LatencyReport>(`/v1/observability/latency${qs({ tenant_id, days })}`);
export const fetchAudit = (tenant_id: string) => get<AuditEntry[]>(`/v1/audit${qs({ tenant_id })}`);
export const fetchRetention = (tenant_id: string) => get<RetentionView>(`/v1/compliance/retention${qs({ tenant_id })}`);
export const gbp = (pence: number) => `£${(pence / 100).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const secs = (s: number | null | undefined) => {
  if (s == null) return "—";
  const a = Math.abs(s);
  const txt = a < 90 ? `${Math.round(a)} s` : a < 5400 ? `${Math.round(a / 60)} min` : `${(a / 3600).toFixed(1)} h`;
  return s < 0 ? `-${txt}` : txt;
};

export const ms = (s: number | null | undefined) =>
  s == null ? "—" : `${Math.round(s * 1000)} ms`;

export const pct = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

export const when = (iso: string) => new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });

// -- Phase 9: outbound & speed-to-lead -----------------------------------------------------------
export type OutboundPurpose = "lead_followup" | "ticket_callback" | "reminder" | "confirmation" | "no_show" | "review_request";
export type OutboundStatus = "scheduled" | "dialing" | "in_progress" | "completed" | "retry" | "exhausted" | "suppressed" | "cancelled" | "failed";
export type LeadStatus = "new" | "calling" | "contacted" | "qualified" | "booked" | "ticketed" | "lost" | "opted_out" | "unreachable";
export type Lead = {
  id: string; tenant_id: string; name: string; phone: string; email: string | null; source: string; interest: string | null;
  notes: string | null; consent: boolean; status: LeadStatus; outbound_call_ids: string[]; created_at: string; first_call_at: string | null;
  speed_to_lead_s: number | null;
};
export type OutboundAttempt = { n: number; at: string; call_id: string | null; outcome: string; detail: string | null };
export type OutboundCall = {
  id: string; tenant_id: string; assistant_id: string; purpose: OutboundPurpose; to: string; name: string | null; lead_id: string | null;
  ticket_id: string | null; scheduled_at: string; status: OutboundStatus; attempts: OutboundAttempt[]; max_attempts: number;
  call_id: string | null; outcome: string | null; outcome_detail: string | null; reason: string | null; created_at: string;
};
export type Suppression = { id: string; tenant_id: string; phone: string; reason: string; source: string; created_at: string };
export type OutboundPolicy = {
  tenant_id: string; enabled: boolean; purposes: OutboundPurpose[]; jurisdiction: string; timezone: string; window_start: string; window_end: string;
  days: string[]; max_attempts: number; retry_gap_min: number; daily_cap_per_number: number; speed_to_lead_target_s: number; require_consent: boolean;
  caller_id: string | null; form_token: string; reminder_hours_before: number; review_request_delay_h: number; leave_voicemail: boolean; opt_out_keywords: string[];
};
export type Jurisdiction = { prefix: string; timezone: string; start: string; end: string; days: string[]; max_attempts: number; note: string };
export type OutboundSummary = {
  jobs: number; leads: number; queued: number; contact_rate: number | null; speed_to_lead_median_s: number | null; speed_to_lead_within_target: number | null;
  leads_by_status: Record<string, number>; by_status: Record<string, number>; by_purpose: Record<string, number>; by_outcome: Record<string, number>; suppressed: number;
};
export const fetchOutboundSummary = (tenant_id: string) => get<OutboundSummary>(`/v1/outbound/summary${qs({ tenant_id })}`);
export const fetchOutboundPolicy = (tenant_id: string) => get<OutboundPolicy>(`/v1/outbound/policy${qs({ tenant_id })}`);
export const fetchJurisdictions = () => get<Record<string, Jurisdiction>>("/v1/outbound/jurisdictions");
export const fetchLeads = (tenant_id: string) => get<Lead[]>(`/v1/outbound/leads${qs({ tenant_id })}`);
export const fetchOutboundCalls = (tenant_id: string) => get<OutboundCall[]>(`/v1/outbound/calls${qs({ tenant_id })}`);
export const fetchSuppressions = (tenant_id: string) => get<Suppression[]>(`/v1/outbound/suppressions${qs({ tenant_id })}`);

// -- Phase 10: live monitoring, takeover & approvals --------------------------------------------

export type SupervisorMode = "none" | "listening" | "taken_over";
export type LiveTranscriptItem = { role: string; text: string; at?: string };
export type LiveCall = {
  call_id: string; tenant_id: string; assistant_id: string; room: string | null; caller: string | null; dialed: string | null;
  direction: string; status: string; started_at: string; answered_at: string | null; transcript: LiveTranscriptItem[];
  escalated: boolean; transfers: number; tickets: number; supervisor: string | null; supervisor_mode: SupervisorMode;
  pending_approval_id: string | null;
};
export type LiveMessage = {
  type: string; tenant_id: string; call_id: string | null; call: LiveCall | null; calls: LiveCall[] | null;
  payload: Record<string, unknown>; at: string;
};
export type JoinInfo = { url: string | null; token: string; room: string; identity: string; mode: SupervisorMode };
export type SupervisorCommand = "whisper" | "say" | "takeover" | "handback" | "hangup";
export type ApprovalKind = "quote" | "booking" | "refund" | "discount" | "other";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "expired";
export type Approval = {
  id: string; tenant_id: string; call_id: string | null; kind: ApprovalKind; title: string; details: string; amount: number | null;
  currency: string; caller: string | null; status: ApprovalStatus; requested_at: string; expires_at: string; decided_at: string | null;
  decided_by: string | null; note: string | null;
};
export type PublicApproval = Omit<Approval, "tenant_id">;

export const fetchLiveCalls = (tenant_id: string) => get<LiveCall[]>(`/v1/live/calls${qs({ tenant_id })}`);
export const joinLiveCall = (tenant_id: string, call_id: string) => request<JoinInfo>(`/v1/live/calls/${call_id}/join${qs({ tenant_id })}`, { method: "POST" });
export const leaveLiveCall = (tenant_id: string, call_id: string) => request<undefined>(`/v1/live/calls/${call_id}/leave${qs({ tenant_id })}`, { method: "POST" });
export const commandLiveCall = (tenant_id: string, call_id: string, cmd: SupervisorCommand, text?: string) =>
  request<JoinInfo | null>(`/v1/live/calls/${call_id}/command${qs({ tenant_id })}`, { method: "POST", body: JSON.stringify({ cmd, text }) });
export const fetchApprovals = (tenant_id: string, status?: ApprovalStatus) => get<Approval[]>(`/v1/approvals${qs({ tenant_id, status })}`);
export const decideApproval = (tenant_id: string, id: string, approve: boolean, note?: string) =>
  request<Approval>(`/v1/approvals/${id}/decide${qs({ tenant_id })}`, { method: "POST", body: JSON.stringify({ approve, note }) });
export const fetchPublicApproval = (token: string) => get<PublicApproval>(`/v1/public/approvals/${token}`);
export const decidePublicApproval = (token: string, approve: boolean, name?: string, note?: string) =>
  request<PublicApproval>(`/v1/public/approvals/${token}`, { method: "POST", body: JSON.stringify({ approve, name, note }) });

/** Browser WebSocket URL for the live stream (auth via query — browsers can't set upgrade headers). */
export async function liveSocketUrl(tenant_id: string): Promise<string> {
  const h = await authHeaders();
  const token = h.authorization?.replace(/^Bearer /, "");
  const base = API_URL.replace(/^http/, "ws");
  return `${base}/v1/live/ws${qs({ tenant_id, token })}`;
}

// -- Phase 11: omnichannel inbox -------------------------------------------------------------------
export type Channel = "call" | "voicemail" | "sms" | "whatsapp" | "webchat";
export type ThreadStatus = "open" | "waiting" | "closed";
export type InboxThread = {
  id: string; tenant_id: string; channel: Channel; identity: string; contact_id: string | null; contact_name: string | null;
  subject: string | null; status: ThreadStatus; assigned_to: string | null; ai_enabled: boolean; unread: number; message_count: number;
  last_preview: string; last_direction: "in" | "out" | "note" | null; last_message_at: string; sla_due_at: string | null;
  sla_breached: boolean; tags: string[]; created_at: string;
};
export type InboxMessage = {
  id: string; thread_id: string; channel: Channel; direction: "in" | "out" | "note"; author: "contact" | "ai" | "agent" | "system";
  author_name: string | null; text: string; call_id: string | null; ticket_id: string | null; status: string; error: string | null; created_at: string;
};
export type InboxStats = { open: number; waiting: number; unread: number; unassigned: number; breached: number; by_channel: Record<string, number> };
export type CannedReply = { id: string; title: string; shortcut: string | null; body: string };
export type ChatWidgetInfo = {
  id: string; token: string; enabled: boolean; title: string; greeting: string; colour: string; allowed_origins: string[]; embed_url: string; snippet: string;
};
export type WhatsAppInfo = { id: string; phone_number_id: string; display_number: string | null; has_token: boolean; verify_token: string; webhook_url: string };
export type ThreadFilters = { status?: ThreadStatus; channel?: Channel; assigned_to?: string; unassigned?: boolean; unread_only?: boolean; q?: string };

const boolq = (b: boolean | undefined) => (b ? "true" : undefined);
export const fetchThreads = (tenant_id: string, f: ThreadFilters = {}) =>
  get<InboxThread[]>(`/v1/inbox/threads${qs({ tenant_id, status: f.status, channel: f.channel, assigned_to: f.assigned_to, unassigned: boolq(f.unassigned), unread_only: boolq(f.unread_only), q: f.q })}`);
export const fetchInboxStats = (tenant_id: string) => get<InboxStats>(`/v1/inbox/stats${qs({ tenant_id })}`);
export const fetchThread = (tenant_id: string, id: string) => get<{ thread: InboxThread; messages: InboxMessage[] }>(`/v1/inbox/threads/${id}${qs({ tenant_id })}`);
export const replyThread = (tenant_id: string, id: string, text: string) => post<InboxMessage>(`/v1/inbox/threads/${id}/reply${qs({ tenant_id })}`, { text });
export const noteThread = (tenant_id: string, id: string, text: string) => post<InboxMessage>(`/v1/inbox/threads/${id}/note${qs({ tenant_id })}`, { text });
export const patchThread = (
  tenant_id: string, id: string,
  body: { status?: ThreadStatus; assigned_to?: string; clear_assignee?: boolean; ai_enabled?: boolean; read?: boolean; tags?: string[]; subject?: string },
) => patch<InboxThread>(`/v1/inbox/threads/${id}${qs({ tenant_id })}`, body);
export const fetchCanned = (tenant_id: string) => get<CannedReply[]>(`/v1/inbox/canned${qs({ tenant_id })}`);
export const createCanned = (tenant_id: string, body: { title: string; shortcut?: string; body: string }) => post<CannedReply>(`/v1/inbox/canned${qs({ tenant_id })}`, body);
export const updateCanned = (tenant_id: string, id: string, body: { title: string; shortcut?: string; body: string }) => put<CannedReply>(`/v1/inbox/canned/${id}${qs({ tenant_id })}`, body);
export const deleteCanned = (tenant_id: string, id: string) => del(`/v1/inbox/canned/${id}${qs({ tenant_id })}`);
export const fetchWidget = (tenant_id: string) => get<ChatWidgetInfo>(`/v1/inbox/widget${qs({ tenant_id })}`);
export const patchWidget = (tenant_id: string, body: { enabled?: boolean; title?: string; greeting?: string; colour?: string; allowed_origins?: string[]; rotate_token?: boolean }) =>
  patch<ChatWidgetInfo>(`/v1/inbox/widget${qs({ tenant_id })}`, body);
export const fetchWhatsApp = (tenant_id: string) => get<WhatsAppInfo | null>(`/v1/inbox/whatsapp${qs({ tenant_id })}`);
export const saveWhatsApp = (tenant_id: string, body: { phone_number_id: string; display_number?: string; access_token?: string }) =>
  put<WhatsAppInfo>(`/v1/inbox/whatsapp${qs({ tenant_id })}`, body);

export type ChatConfig = { title: string; greeting: string; colour: string; enabled: boolean };
export type ChatMessage = { id: string; direction: string; author: string; author_name: string | null; text: string; created_at: string };
export const fetchChatConfig = (token: string) => get<ChatConfig>(`/v1/public/chat/${token}`);
export const sendChat = (token: string, visitor: string, text: string, name?: string) => post<ChatMessage[]>(`/v1/public/chat/${token}/messages`, { visitor, text, name });
export const pollChat = (token: string, visitor: string) => get<ChatMessage[]>(`/v1/public/chat/${token}/messages${qs({ visitor })}`);
