export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
export const TOKEN_COOKIE = "parlio_token";
export const MFA_COOKIE = "parlio_mfa";
export const VIEW_AS_COOKIE = "parlio_view_as";

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
  transcript: { role: string; text: string; interrupted?: boolean; at?: string }[];
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
  thread_id: string | null;
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
  department_notes: Record<string, string>;
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
  role: "owner" | "admin" | "member" | "viewer" | StaffRole;
  status: "active" | "invited";
  invited_at: string | null;
};
export type StaffRole = "owner" | "support" | "finance" | "readonly";

export type Me = {
  user_id: string;
  email: string;
  name: string | null;
  mode: "dev" | "supabase";
  memberships: Member[];
  auth: { mode: "dev" | "supabase"; supabase_url: string | null };
  staff_role: StaffRole | null;
  view_as: string | null;
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
  transcript: { role: string; text: string; at?: string }[];
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
  const h: Record<string, string> = token ? { authorization: `Bearer ${decodeURIComponent(token)}` } : {};
  const mfa = await readCookie(MFA_COOKIE);
  if (mfa) h["x-parlio-mfa"] = mfa;
  const viewAs = await readCookie(VIEW_AS_COOKIE);
  if (viewAs) h["x-parlio-view-as"] = viewAs;
  return h;
}

async function readCookie(name: string): Promise<string | undefined> {
  if (typeof window === "undefined") {
    const { cookies } = await import("next/headers");
    return (await cookies()).get(name)?.value;
  }
  const raw = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`))?.slice(name.length + 1);
  return raw ? decodeURIComponent(raw) : undefined;
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
  features: string[]; entitlements: string[]; enterprise: boolean; trial_days?: number | null;
};
export type Entitlements = { catalogue: Record<string, string>; enabled: Record<string, boolean> };
export type SubscriptionStatus = "trialing" | "active" | "past_due" | "paused" | "suspended" | "cancelled";
export type Subscription = {
  tenant_id: string; plan_id: string; status: SubscriptionStatus; period_start: string; period_end: string; trial_ends_at?: string | null;
  coupon: string | null; coupon_months_left: number | null; provider: string; customer_ref: string | null; subscription_ref: string | null;
  status_reason?: string | null; created_at?: string;
};
export type Credit = { id: string; tenant_id: string; pence: number; remaining_pence: number; reason: string; granted_by: string; created_at: string };
export type Invoice = {
  id: string; tenant_id: string; period_start: string; period_end: string; total_pence: number; status: string; provider: string; url: string | null; created_at: string;
};
export type Refund = {
  id: string; tenant_id: string; pence: number; reason: string; invoice_id: string | null; provider: string; provider_ref: string | null; issued_by: string; created_at: string;
};
export type TenantLimits = {
  tenant_id: string; max_concurrent_calls: number | null; minutes_cap: number | null; rate_limit_per_minute: number | null; note: string | null;
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
export const createAssistant = (body: { name: string; business_name: string; copy_from?: string | null }) =>
  request<Assistant>("/v1/assistants", { method: "POST", body: JSON.stringify(body) });
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

/** Browser-only: fetch one recording leg (auth headers) and return an object URL for <audio>/download. */
export async function fetchRecordingUrl(call_id: string, index: number): Promise<ApiResult<string>> {
  const res = await fetch(`${API_URL}/v1/calls/${call_id}/recordings/${index}`, { headers: await authHeaders() });
  if (!res.ok) {
    const detail = await res.json().then((j: { detail?: string }) => j.detail).catch(() => undefined);
    return { ok: false, status: res.status, error: detail ?? res.statusText };
  }
  return { ok: true, data: URL.createObjectURL(await res.blob()) };
}

/** Browser-only: fetch a CSV export with auth headers and trigger a file download. */
export async function downloadCsv(what: "calls" | "contacts" | "tickets" | "audit", tenant_id: string): Promise<string | null> {
  const path = what === "audit" ? "/v1/compliance/audit.csv" : `/v1/export/${what}.csv`;
  const res = await fetch(`${API_URL}${path}${qs({ tenant_id })}`, { headers: await authHeaders() });
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
  subject: string | null; status: ThreadStatus; assigned_to: string | null; ai_enabled: boolean; handoff_department: string | null; callback_ticket_id: string | null; ticket_ids: string[]; unread: number; message_count: number;
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
  id: string; token: string; enabled: boolean; voice_enabled: boolean; title: string; greeting: string; colour: string; allowed_origins: string[]; embed_url: string; snippet: string;
};
export type WhatsAppInfo = { id: string; phone_number_id: string; display_number: string | null; has_token: boolean; verify_token: string; webhook_url: string };
export type ThreadFilters = { status?: ThreadStatus; channel?: Channel; assigned_to?: string; unassigned?: boolean; unread_only?: boolean; q?: string };

const boolq = (b: boolean | undefined) => (b ? "true" : undefined);
export const fetchThreads = (tenant_id: string, f: ThreadFilters = {}) =>
  get<InboxThread[]>(`/v1/inbox/threads${qs({ tenant_id, status: f.status, channel: f.channel, assigned_to: f.assigned_to, unassigned: boolq(f.unassigned), unread_only: boolq(f.unread_only), q: f.q })}`);
export const fetchInboxStats = (tenant_id: string) => get<InboxStats>(`/v1/inbox/stats${qs({ tenant_id })}`);

// -- sidebar activity badges ------------------------------------------------------------------------
export type NavBadges = { live: number; inbox: number; tickets: number; transfers: number; outbound: number; support: number; total: number };
export const fetchNavBadges = (tenant_id: string) => get<NavBadges>(`/v1/nav/badges${qs({ tenant_id })}`);
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
export const patchWidget = (tenant_id: string, body: { enabled?: boolean; voice_enabled?: boolean; title?: string; greeting?: string; colour?: string; allowed_origins?: string[]; rotate_token?: boolean }) =>
  patch<ChatWidgetInfo>(`/v1/inbox/widget${qs({ tenant_id })}`, body);
export const fetchWhatsApp = (tenant_id: string) => get<WhatsAppInfo | null>(`/v1/inbox/whatsapp${qs({ tenant_id })}`);
export const saveWhatsApp = (tenant_id: string, body: { phone_number_id: string; display_number?: string; access_token?: string }) =>
  put<WhatsAppInfo>(`/v1/inbox/whatsapp${qs({ tenant_id })}`, body);

export type ChatConfig = { title: string; greeting: string; colour: string; enabled: boolean; voice_enabled: boolean };
export type WebVoiceSession = { call_id: string; room: string; identity: string; url: string | null; token: string; simulated: boolean };
export const startChatVoice = (token: string, visitor: string, name?: string, page_url?: string) =>
  post<WebVoiceSession>(`/v1/public/chat/${token}/voice`, { visitor, name, page_url });
export type ChatMessage = { id: string; direction: string; author: string; author_name: string | null; text: string; created_at: string };
export const fetchChatConfig = (token: string) => get<ChatConfig>(`/v1/public/chat/${token}`);
export const sendChat = (token: string, visitor: string, text: string, name?: string) => post<ChatMessage[]>(`/v1/public/chat/${token}/messages`, { visitor, text, name });
export const pollChat = (token: string, visitor: string) => get<ChatMessage[]>(`/v1/public/chat/${token}/messages${qs({ visitor })}`);
export type ChatStatus = "none" | "ai" | "waiting" | "human" | "closed";
export type ChatState = { status: ChatStatus; agent_name: string | null; department: string | null; messages: ChatMessage[] };
export const pollChatState = (token: string, visitor: string) => get<ChatState>(`/v1/public/chat/${token}/state${qs({ visitor })}`);

// -- Phase 13: quality, insights, simulation, voice cloning --------------------------------------
export type QAFlag = "hallucination" | "unanswered" | "rude" | "escalated" | "long_silence" | "unresolved" | string;
export type QAScore = {
  call_id: string; tenant_id: string; assistant_id: string; resolution: number; tone: number; accuracy: number;
  hallucination_risk: number; overall: number; flags: QAFlag[]; unanswered: string[]; notes: string[]; scorer: string; created_at: string;
};
export type QASettings = { tenant_id: string; enabled: boolean; alert_below: number; alert_on_hallucination: boolean; min_turns: number };
export type QAStats = {
  scored: number; avg_overall: number | null; avg_resolution: number | null; avg_tone: number | null; avg_accuracy: number | null;
  avg_hallucination_risk: number | null; low_score_calls: number; flagged_hallucinations: number; unanswered_questions: number;
};
export type Insight = {
  id: string; tenant_id: string; assistant_id: string; kind: "faq" | "rule"; question: string; examples: string[]; call_ids: string[]; count: number;
  suggested_answer: string | null; suggested_rule: string | null; status: "open" | "applied" | "dismissed"; applied_version: number | null; created_at: string; updated_at: string;
};
export type QAOverview = { settings: QASettings; stats: QAStats; recent: QAScore[]; insights: Insight[] };
export type Expectation = { mentions: string[]; avoids: string[]; handoff: boolean | null; ticket: boolean | null; min_overall: number };
export type Scenario = { id: string; tenant_id: string; name: string; persona: string; goal: string; turns: string[]; expect: Expectation; created_at: string };
export type SimTurn = { caller: string; assistant: string; handoff: boolean; ticket: boolean };
export type SimulationResult = {
  scenario_id: string; scenario_name: string; label: string; config_source: string; turns: SimTurn[]; score: QAScore; passed: boolean; failures: string[]; agent: string;
};
export type SimulationRun = { id: string; tenant_id: string; assistant_id: string; results: SimulationResult[]; winner: string | null; created_at: string };
export type VoiceClone = {
  id: string; tenant_id: string; assistant_id: string; name: string; consent_by: string; consent_statement: string; consent_at: string; sample_seconds: number;
  provider: string; provider_voice_id: string | null; status: "pending" | "ready" | "active" | "failed" | string; error: string | null; created_at: string;
};
export type VoiceCloneView = { consent_statement: string; provider: string; live: boolean; clones: VoiceClone[] };
export const fetchQuality = (tenant_id: string) => get<QAOverview>(`/v1/quality/overview${qs({ tenant_id })}`);
export const fetchCallScore = (tenant_id: string, call_id: string) => get<QAScore>(`/v1/quality/calls/${call_id}${qs({ tenant_id })}`);
export const fetchScenarios = (tenant_id: string) => get<Scenario[]>(`/v1/quality/scenarios${qs({ tenant_id })}`);
export const fetchSimRuns = (tenant_id: string) => get<SimulationRun[]>(`/v1/quality/simulate/runs${qs({ tenant_id })}`);
export const fetchVoiceClones = (tenant_id: string) => get<VoiceCloneView>(`/v1/quality/voice-clones${qs({ tenant_id })}`);

// -- Phase 14: value, white-label, compliance pack, security ---------------------------------------
export type LeadScore = { call_id: string; score: number; grade: "hot" | "warm" | "cold" | string; intent: string | null; reasons: string[] };
export type ValueSettings = {
  tenant_id: string; currency: string; avg_job_value_pence: number; booking_value_pence: number | null; lead_to_sale_rate: number;
  missed_call_lead_rate: number; digest_enabled: boolean; digest_weekday: number; digest_hour: number;
};
export type TrackingNumber = { id: string; tenant_id: string; e164: string; channel: string; campaign: string | null; monthly_cost_pence: number; created_at: string };
export type ChannelValue = { channel: string; calls: number; leads: number; bookings: number; attributed_pence: number; cost_pence: number; cost_per_lead_pence: number | null };
export type ValueReport = {
  tenant_id: string; period_start: string; period_end: string; currency: string; calls: number; answered: number; missed: number; qualified_leads: number; hot_leads: number;
  bookings: number; attributed_pence: number; pipeline_pence: number; missed_revenue_pence: number; recovered_by_ai_pence: number; intents: Record<string, number>;
  channels: ChannelValue[]; top_leads: LeadScore[]; generated_at: string;
};
export type DigestRecord = { id: string; tenant_id: string; title: string; body: string; report: Record<string, unknown>; sent_at: string };
export type ValueOverview = { settings: ValueSettings; report: ValueReport; tracking_numbers: TrackingNumber[]; digests: DigestRecord[] };
export type Branding = {
  tenant_id: string; brand_name: string; logo_url: string | null; icon_url: string | null; primary_colour: string; accent_colour: string; support_email: string | null;
  support_url: string | null; hide_powered_by: boolean; custom_domain: string | null; domain_verified: boolean; updated_at: string;
};
export type DomainInstructions = { domain: string; cname_target: string; txt_name: string; txt_value: string; verified: boolean };
export type TenantLink = { id: string; parent_tenant_id: string; child_tenant_id: string; client_name: string; inherit_branding: boolean; notes: string | null; created_at: string };
export type ClientSummary = { link: TenantLink; usage: UsageSummary | null; members: number; assistants: number };
export type BrandingView = { branding: Branding; domain: DomainInstructions | null; is_agency: boolean; parent: TenantLink | null };
export type SsoConfig = {
  provider: "none" | "microsoft_entra" | "google_workspace" | "okta" | "saml" | "oidc"; protocol: string; domains: string[]; issuer: string | null; client_id: string | null;
  metadata_url: string | null; enforce: boolean; supabase_provider_id: string | null;
};
export type ScimConfig = { enabled: boolean; default_role: string; token_hint: string | null; issued_at: string | null };
export type SecurityPolicy = { tenant_id: string; require_2fa: "off" | "admins" | "all"; session_hours: number; sso: SsoConfig; scim: ScimConfig; updated_at: string };
export type SecurityView = { policy: SecurityPolicy; sso_status: string; scim_endpoint: string; members_without_2fa: string[] };
export type AssistantRegion = {
  assistant_id: string; name: string; region_profile: string; stt: string[]; llm: string[]; tts: string[]; consent_announcement: boolean; recording_enabled: boolean;
};
export type CompliancePack = {
  tenant_id: string; generated_at: string; data_residency: string; assistants: AssistantRegion[]; retention: RetentionPolicy; security: SecurityPolicy;
  sub_processors: Record<string, string>[]; consent: Record<string, unknown>; audit_entries: number; notes: string[];
};
export type TwoFactorStatus = { enrolled: boolean; confirmed: boolean; recovery_codes_left: number; mfa_verified: boolean };
export type UserSession = {
  id: string; user_id: string; label: string; ip: string | null; mfa: boolean; created_at: string; last_seen_at: string; expires_at: string; revoked_at: string | null; current: boolean;
};
export type MfaToken = { token: string; session_id: string; expires_at: string };
export const fetchValue = (tenant_id: string, days = 7) => get<ValueOverview>(`/v1/value${qs({ tenant_id, days })}`);
export const fetchWhiteLabel = (tenant_id: string) => get<BrandingView>(`/v1/whitelabel${qs({ tenant_id })}`);
export const fetchClients = (tenant_id: string) => get<ClientSummary[]>(`/v1/whitelabel/clients${qs({ tenant_id })}`);
export const fetchCompliancePack = (tenant_id: string) => get<CompliancePack>(`/v1/compliance/pack${qs({ tenant_id })}`);
export const fetchSecurity = (tenant_id: string) => request<SecurityView>(`/v1/security${qs({ tenant_id })}`);
export const fetchTwoFactor = () => get<TwoFactorStatus>("/v1/account/2fa");
export const fetchSessions = () => get<UserSession[]>("/v1/account/sessions");
export const money = (pence: number, currency = "GBP") =>
  new Intl.NumberFormat("en-GB", { style: "currency", currency, maximumFractionDigits: 0 }).format(pence / 100);

// -- platform admin (staff only) --------------------------------------------------------------
export type StaffSettings = { ip_allowlist: string[]; view_as_ttl_minutes: number; updated_by: string | null; updated_at: string };
export type FeatureFlags = { tenant_id: string; flags: Record<string, boolean>; updated_by: string | null; updated_at: string };
export type SupportNote = { id: string; tenant_id: string; author: string; text: string; pinned: boolean; created_at: string };
export type PlatformStatusLevel = "ok" | "degraded" | "incident" | "maintenance";
export type PlatformStatus = {
  level: PlatformStatusLevel; title: string; message: string; link: string | null; starts_at: string | null; ends_at: string | null;
  updated_by: string | null; updated_at: string;
};
export type PublicStatus = { level: string; title: string; message: string; link: string | null; active: boolean };
export type ViewAsGrant = { token: string; tenant_id: string; staff_email: string; expires_at: string; read_only: boolean };
export type TenantHealthGrade = "healthy" | "watch" | "at_risk" | "inactive";
export type TenantSummary = {
  tenant_id: string; name: string; created_at: string; plan_id: string; plan_name: string; status: SubscriptionStatus; trial_ends_at: string | null;
  assistants: number; members: number; numbers: number; calls_period: number; minutes_period: number; minutes_included: number;
  estimated_total_pence: number; credit_balance_pence: number; last_call_at: string | null; flags: string[]; health: TenantHealthGrade;
};
export type AssistantBrief = { id: string; name: string; business_name: string; version: number | null; updated_at: string | null };
export type TenantDetail = {
  summary: TenantSummary; subscription: Subscription; usage: UsageSummary; limits: TenantLimits; credits: Credit[]; invoices: Invoice[]; refunds: Refund[];
  assistants: AssistantBrief[]; members: Member[]; numbers: Record<string, unknown>[]; trunks: Record<string, unknown>[]; connectors: Record<string, unknown>[];
  flags: FeatureFlags; notes: SupportNote[]; audit: AuditEntry[];
};
export type SeriesPoint = { key: string; value: number; extra: Record<string, number> };
export type BusinessAnalytics = {
  tenants: number; signups_period: number; trialing: number; active: number; past_due: number; paused: number; suspended: number; cancelled: number;
  conversions_period: number; churned_period: number; trial_conversion_pct: number | null; plan_mix: Record<string, number>; mrr_pence: number; arr_pence: number;
  overage_pence_period: number; credit_outstanding_pence: number; top_accounts: Record<string, unknown>[]; signups_by_day: SeriesPoint[]; cohorts: Record<string, unknown>[];
};
export type DemandAnalytics = {
  calls: number; minutes: number; inbound: number; outbound: number; answered: number; missed: number; failed: number; transferred: number; ticketed: number;
  channel_mix: Record<string, number>; calls_by_day: SeriesPoint[]; calls_by_hour: SeriesPoint[]; peak_concurrency: number; capacity_concurrent: number;
  growth_pct: number | null; forecast_calls_next_period: number;
};
export type QualityCostAnalytics = {
  answer_latency_p50_s: number | null; answer_latency_p95_s: number | null; turn_latency_p50_ms: number | null; turn_latency_p95_ms: number | null;
  vendor_cost_pence: number; revenue_pence: number; gross_margin_pct: number | null; cost_per_call_pence: number | null; qa_overall_avg: number | null;
  qa_by_day: SeriesPoint[]; low_score_calls: number; connector_jobs: number; connector_failed: number; connector_failure_pct: number | null;
  connectors_by_provider: Record<string, number>; tenant_health: Record<string, number>;
};
export type PlatformAnalytics = { generated_at: string; days: number; business: BusinessAnalytics; demand: DemandAnalytics; quality: QualityCostAnalytics };
export type AdminOverview = { status: PlatformStatus; analytics: PlatformAnalytics; staff: number; recent_audit: AuditEntry[] };

export const fetchAdminOverview = (days = 30) => request<AdminOverview>(`/v1/admin/overview${qs({ days })}`);
export const fetchAdminAnalytics = (days = 30) => get<PlatformAnalytics>(`/v1/admin/analytics${qs({ days })}`);
export const fetchAdminActivity = (limit = 200) => get<AuditEntry[]>(`/v1/admin/activity${qs({ limit })}`);
export const fetchAdminTenants = (params: { q?: string; sub_status?: string; plan_id?: string } = {}) =>
  get<TenantSummary[]>(`/v1/admin/tenants${qs(params)}`);
export const fetchAdminTenant = (tenant_id: string) => request<TenantDetail>(`/v1/admin/tenants/${tenant_id}`);
export const fetchFeatureFlagCatalogue = () => get<Record<string, string>>("/v1/admin/feature-flags");
export const fetchAdminPlans = () => get<Plan[]>("/v1/admin/plans");
export const fetchAdminPlanDefaults = () => get<{ trial_days: number }>("/v1/admin/plans/defaults");
export const fetchEntitlementCatalogue = () => get<Record<string, string>>("/v1/admin/entitlements");
export const fetchEntitlements = (tenantId: string) => get<Entitlements>(`/v1/billing/entitlements?tenant_id=${encodeURIComponent(tenantId)}`);
export const fetchAdminCoupons = () => get<Coupon[]>("/v1/admin/coupons");
export const fetchStaff = () => get<Member[]>("/v1/admin/staff");
export const fetchStaffSettings = () => get<StaffSettings>("/v1/admin/staff/settings");
export const fetchPlatformStatus = () => get<PlatformStatus>("/v1/admin/status");
export const fetchPublicStatus = () => get<PublicStatus>("/v1/public/status");
export const adminCsvUrl = (what: "tenants" | "analytics", days = 30) => `${API_URL}/v1/admin/export/${what}.csv${qs({ days })}`;
export async function fetchAdminCsv(what: "tenants" | "analytics", days = 30): Promise<string | null> {
  try {
    const res = await fetch(adminCsvUrl(what, days), { cache: "no-store", headers: await authHeaders() });
    return res.ok ? await res.text() : null;
  } catch {
    return null;
  }
}

// -- Phase 17/18: ops health, status page, support desk -----------------------------------------
export type HealthSignal = { key: string; label: string; value: number | null; unit: string; ok: boolean; weight: number; detail: string };
export type TenantHealth = {
  tenant_id: string; name: string; score: number; grade: "healthy" | "watch" | "at_risk" | "critical" | "inactive";
  signals: HealthSignal[]; open_alerts: number; calls_period: number; days: number; computed_at: string;
};
export type OpsAlert = {
  id: string; tenant_id: string; kind: string; severity: "info" | "warning" | "critical"; title: string; detail: string;
  evidence: Record<string, unknown>; opened_at: string; resolved_at: string | null; acknowledged_by: string | null; acknowledged_at: string | null; paged: boolean;
};
export type SyntheticRun = {
  id: string; tenant_id: string; assistant_id: string; trigger: string; mode: string; passed: boolean;
  checks: { path: string; passed: boolean; detail: string }[]; ticket_id: string | null; duration_ms: number; created_at: string;
};
export type ForwardingHealth = {
  tenant_id: string; status: "ok" | "no_baseline" | "quiet" | "forwarding_may_be_off" | "outside_hours";
  expected_calls_per_open_hour: number; hours_since_last_inbound: number | null; open_now: boolean; detail: string; guide_id: string;
};
export type TrunkHealth = {
  trunk_id: string; name: string; mode: string; status: string; registration: string; registration_detail: string | null;
  options_ping_ok: boolean | null; options_rtt_ms: number | null; invites: number; invite_failures: number; auth_failures: number; invite_failure_pct: number | null;
  audio: { calls: number; mos_avg: number | null; jitter_ms_avg: number | null; packet_loss_pct_avg: number | null; one_way_audio: number; codec_mismatch: number; dtmf_mismatch: number };
  healthy: boolean; issues: string[]; remediation: string[];
};
export type FaultReport = {
  id: string; tenant_id: string; subject: string; attribution: "parlio" | "carrier" | "customer_provider" | "customer_config" | "unknown"; confidence: number;
  headline: string; explanation: string; next_steps: string[]; evidence: Record<string, unknown>; provider_report: string; created_at: string;
};
export type HealthView = { health: TenantHealth; forwarding: ForwardingHealth; trunks: TrunkHealth[]; alerts: OpsAlert[]; synthetic: SyntheticRun[]; faults: FaultReport[] };
export type ComponentState = "operational" | "degraded" | "partial_outage" | "major_outage" | "maintenance";
export type ComponentStatus = { id: string; name: string; state: ComponentState; detail: string; source: string };
export type IncidentUpdate = { at: string; status: "investigating" | "identified" | "monitoring" | "resolved"; message: string; by: string | null };
export type Incident = {
  id: string; title: string; severity: "p1" | "p2" | "p3"; components: string[]; impact: ComponentState; status: IncidentUpdate["status"];
  updates: IncidentUpdate[]; started_at: string; resolved_at: string | null; rca_due_at: string | null; rca: string | null; created_by: string | null;
};
export type StatusPage = { overall: ComponentState; components: ComponentStatus[]; incidents: Incident[]; uptime_30d_pct: number; generated_at: string };
export type OnCallConfig = { provider: "none" | "pagerduty" | "opsgenie" | "webhook"; routing_key: string | null; webhook_url: string | null; page_on: string[]; rota: string[]; updated_by: string | null; updated_at: string };
export type FailoverState = { primary_carrier: string; secondary_carrier: string; active: string; auto: boolean; region: string; last_switch_at: string | null; reason: string | null; updated_by: string | null };
export type OpsOverview = {
  board: TenantHealth[]; alerts: Record<string, number>; open_alerts: OpsAlert[]; status: StatusPage; oncall: OnCallConfig; failover: FailoverState;
  canary_ok: boolean; canary_reasons: string[]; open_tickets: number; breached_tickets: number;
};
export type SupportPriority = "p1" | "p2" | "p3" | "p4";
export type SupportStatus = "open" | "in_progress" | "waiting_customer" | "waiting_provider" | "resolved" | "closed";
export type SupportEvent = { at: string; type: string; by: string; text: string; public: boolean };
export type SupportTicket = {
  id: string; tenant_id: string; requester: string; channel: string; subject: string; body: string; priority: SupportPriority; status: SupportStatus;
  tags: string[]; assignee: string | null; sla_due_at: string | null; sla_breached: boolean; first_response_at: string | null; fault: FaultReport | null;
  provider_email: string | null; provider_consent: boolean; provider_emailed_at: string | null; engineering_ref: string | null;
  csat_score: number | null; csat_comment: string | null; events: SupportEvent[]; created_at: string; updated_at: string; resolved_at: string | null;
};
export type KbArticle = { id: string; title: string; body: string; tags: string[]; url: string | null };
export type TagReview = {
  days: number; tickets: number; resolved: number; csat_avg: number | null; csat_responses: number; sla_breaches: number; median_first_response_min: number | null;
  by_tag: Record<string, number>; by_priority: Record<string, number>; by_channel: Record<string, number>;
};

export const fetchHealthView = (tenant_id: string) => get<HealthView>(`/v1/health/overview${qs({ tenant_id })}`);
export const runSynthetic = (tenant_id: string) => request<SyntheticRun>(`/v1/health/synthetic${qs({ tenant_id })}`, { method: "POST" });
export const diagnoseCall = (tenant_id: string, call_id: string) => request<FaultReport>(`/v1/health/calls/${call_id}/diagnose${qs({ tenant_id })}`, { method: "POST" });
export const diagnoseTrunk = (tenant_id: string, trunk_id: string) => request<FaultReport>(`/v1/health/trunks/${trunk_id}/diagnose${qs({ tenant_id })}`, { method: "POST" });
export const diagnoseForwarding = (tenant_id: string) => request<FaultReport>(`/v1/health/forwarding/diagnose${qs({ tenant_id })}`, { method: "POST" });
export const fetchSupportTickets = (tenant_id: string) => get<SupportTicket[]>(`/v1/support/tickets${qs({ tenant_id })}`);
export const openSupportTicket = (tenant_id: string, body: { subject: string; body: string; priority: SupportPriority }) =>
  request<SupportTicket>(`/v1/support/tickets${qs({ tenant_id })}`, { method: "POST", body: JSON.stringify(body) });
export const replySupportTicket = (tenant_id: string, id: string, text: string) =>
  request<SupportTicket>(`/v1/support/tickets/${id}/reply${qs({ tenant_id })}`, { method: "POST", body: JSON.stringify({ text }) });
export const rateSupportTicket = (tenant_id: string, id: string, score: number, comment?: string) =>
  request<SupportTicket>(`/v1/support/tickets/${id}/csat${qs({ tenant_id })}`, { method: "POST", body: JSON.stringify({ score, comment }) });
export const fetchKb = (tenant_id: string, q = "") => get<KbArticle[]>(`/v1/support/kb${qs({ tenant_id, q })}`);
export const fetchPublicStatusPage = () => get<StatusPage>("/v1/public/status-page");
export const fetchOpsOverview = () => get<OpsOverview>("/v1/admin/ops/overview");
export const fetchOpsTenant = (tenant_id: string) => get<HealthView>(`/v1/admin/ops/tenants/${tenant_id}`);
export const fetchIncidents = () => get<Incident[]>("/v1/admin/ops/incidents");
export const fetchDeskTickets = (open_only = false) => get<SupportTicket[]>(`/v1/admin/ops/support/tickets${qs({ open_only: open_only ? "true" : undefined })}`);
export const fetchTagReview = (days = 7) => get<TagReview>(`/v1/admin/ops/support/tag-review${qs({ days })}`);

// -- Phase 19: guided journey, setup checklist, explainability, trust centre --------------------
export type CallVolume = "0-50" | "50-200" | "200-500" | "500+";
export type JourneyTask = "faqs" | "book" | "transfer" | "messages" | "info" | "qualify" | "payments" | "outbound" | "webchat";
export type JourneyChannel = "phone" | "sms" | "whatsapp" | "webchat";
export type Vertical = "trades" | "salon" | "hospitality" | "professional" | "dental" | "legal" | "property" | "general";
export type Questionnaire = {
  monthly_calls: CallVolume; tasks: JourneyTask[]; team_size: number; channels: JourneyChannel[]; languages: string[];
  integrations: string[]; vertical: Vertical; sovereign_uk: boolean; locations: number;
};
export type PlanOption = { plan_id: string; name: string; monthly_pence: number; included_minutes: number; fits: boolean; missing: string[]; estimated_monthly_pence: number };
export type PlanRecommendation = { plan_id: string; reasons: string[]; needed_entitlements: string[]; estimated_minutes: number; options: PlanOption[]; trial_days: number };
export type VerticalPlaybook = { id: Vertical; name: string; tagline: string; greeting: string; faqs: Faq[]; rules: BusinessRule[]; required_fields: string[]; suggested_tasks: JourneyTask[] };
export type ChecklistItem = { key: string; title: string; detail: string; done: boolean; href: string; optional: boolean };
export type SetupChecklist = {
  tenant_id: string; items: ChecklistItem[]; completed: number; total: number; live: boolean; trial_ends_at: string | null; plan_id: string; next_step: ChecklistItem | null;
};
export type QuestionnaireOut = { tenant_id: string; questionnaire: Questionnaire | null; recommended_plan_id: string | null };
export type Evidence = { kind: "faq" | "rule" | "business" | "hours" | "greeting" | "instructions" | "none"; label: string; text: string; score: number };
export type ExplainedTurn = { index: number; text: string; evidence: Evidence[] };
export type CallExplanation = { call_id: string; assistant_id: string; assistant_version: number | null; turns: ExplainedTurn[]; note: string };
export type TrustSection = { id: string; title: string; body: string };
export type TrustCentre = { updated_at: string; data_residency: string; sub_processors: Record<string, string>[]; sections: TrustSection[]; status_url: string };
export type OnboardingResult = { tenant_id: string; assistant: Assistant; member: Member; plan_id: string | null; trial_ends_at: string | null };

export const recommendPlan = (q: Questionnaire) => request<PlanRecommendation>("/v1/onboarding/recommend", { method: "POST", body: JSON.stringify(q) });
export const fetchVerticals = () => get<VerticalPlaybook[]>("/v1/onboarding/verticals");
export const fetchChecklist = (tenant_id: string) => get<SetupChecklist>(`/v1/setup/checklist${qs({ tenant_id })}`);
export const fetchQuestionnaire = (tenant_id: string) => get<QuestionnaireOut>(`/v1/setup/questionnaire${qs({ tenant_id })}`);
export const fetchExplanation = (call_id: string) => request<CallExplanation>(`/v1/calls/${call_id}/explain`);
export const fetchTrustCentre = () => get<TrustCentre>("/v1/public/trust");

// -- Phase 19b: white-glove, first-week digest, FAQ import, announcements / roadmap / feedback ----
export type WhiteGloveArea = "config_review" | "forwarding" | "sip" | "test_calls" | "integrations" | "team_training";
export type WhiteGloveStatus = "requested" | "scheduled" | "in_progress" | "completed" | "cancelled";
export type WhiteGloveRequest = {
  id: string; tenant_id: string; requested_by: string; status: WhiteGloveStatus; assigned_to: string | null; scheduled_at: string | null;
  contact_name: string; contact_email: string; contact_phone: string | null; areas: WhiteGloveArea[]; preferred_slots: string[]; notes: string;
  staff_notes: { author: string; text: string; at: string }[]; created_at: string; updated_at: string;
};
export type WhiteGloveEligibility = { eligible: boolean; reason: string | null; open_request: WhiteGloveRequest | null };
export type WhiteGloveRequestIn = {
  contact_name: string; contact_email: string; contact_phone?: string | null; areas: WhiteGloveArea[]; preferred_slots: string[]; notes: string;
};
export const fetchWhiteGlove = (tenant_id: string) => get<WhiteGloveEligibility>(`/v1/whiteglove${qs({ tenant_id })}`);
export const fetchWhiteGloveAreas = () => get<Record<string, string>>("/v1/whiteglove/areas");
export const requestWhiteGlove = (tenant_id: string, body: WhiteGloveRequestIn) =>
  request<WhiteGloveRequest>(`/v1/whiteglove${qs({ tenant_id })}`, { method: "POST", body: JSON.stringify(body) });
export const cancelWhiteGlove = (tenant_id: string, id: string) =>
  request<WhiteGloveRequest>(`/v1/whiteglove/${id}${qs({ tenant_id })}`, { method: "DELETE" });
export const fetchWhiteGloveQueue = () => get<WhiteGloveRequest[]>("/v1/admin/whiteglove");
export const updateWhiteGlove = (id: string, body: { status?: WhiteGloveStatus; assigned_to?: string; scheduled_at?: string; note?: string }) =>
  patch<WhiteGloveRequest>(`/v1/admin/whiteglove/${id}`, body);

export type FirstWeekReport = {
  tenant_id: string; signed_up_at: string | null; days_live: number; calls: number; answered: number; missed: number; after_hours: number; minutes: number;
  bookings: number; qualified_leads: number; attributed_pence: number; currency: string; top_intents: [string, number][];
  checklist_completed: number; checklist_total: number; highlights: string[]; next_steps: string[]; generated_at: string;
};
export const fetchFirstWeek = (tenant_id: string) => get<FirstWeekReport>(`/v1/setup/first-week${qs({ tenant_id })}`);

export type FaqSource = "text" | "csv" | "url";
export type FaqImportResult = { source: FaqSource; suggested: Faq[]; duplicates: Faq[] };
export const importFaqs = (assistant_id: string, body: { source: FaqSource; content: string; category?: string }) =>
  request<FaqImportResult>(`/v1/assistants/${assistant_id}/faqs/import`, { method: "POST", body: JSON.stringify(body) });
export const applyFaqs = (assistant_id: string, faqs: Faq[]) =>
  request<{ added: number; total: number; config: Assistant }>(`/v1/assistants/${assistant_id}/faqs/apply`, { method: "POST", body: JSON.stringify({ faqs }) });

export type AnnouncementKind = "feature" | "improvement" | "fix" | "notice";
export const KIND_LABEL: Record<AnnouncementKind, string> = { feature: "New", improvement: "Improved", fix: "Fixed", notice: "Notice" };
export type Announcement = {
  id: string; title: string; body: string; kind: AnnouncementKind; pinned: boolean; published: boolean; link: string | null; author: string; created_at: string; updated_at: string;
};
export type AnnouncementIn = { title: string; body: string; kind: AnnouncementKind; pinned: boolean; published: boolean; link: string | null };
export type AnnouncementFeed = { unread: number; items: (Announcement & { read: boolean })[] };
export const fetchAnnouncements = (tenant_id: string) => get<AnnouncementFeed>(`/v1/announcements${qs({ tenant_id })}`);
export const markAnnouncementsRead = (tenant_id: string, announcement_id?: string) =>
  post<AnnouncementFeed>(`/v1/announcements/read${qs({ tenant_id })}`, { announcement_id: announcement_id ?? null });
export const fetchChangelog = () => get<Announcement[]>("/v1/public/changelog");
export const fetchAdminAnnouncements = () => get<Announcement[]>("/v1/admin/announcements");
export const createAnnouncement = (body: AnnouncementIn) => request<Announcement>("/v1/admin/announcements", { method: "POST", body: JSON.stringify(body) });
export const updateAnnouncement = (id: string, body: AnnouncementIn) => put<Announcement>(`/v1/admin/announcements/${id}`, body);
export const deleteAnnouncement = (id: string) => del(`/v1/admin/announcements/${id}`);

export type RoadmapStatus = "considering" | "planned" | "in_progress" | "shipped";
export const ROADMAP_LABEL: Record<RoadmapStatus, string> = { considering: "Considering", planned: "Planned", in_progress: "In progress", shipped: "Shipped" };
export type RoadmapItem = { id: string; title: string; description: string; status: RoadmapStatus; category: string; eta: string | null; votes: number; created_at: string; updated_at: string };
export type RoadmapView = RoadmapItem & { voted: boolean };
export type RoadmapItemIn = { title: string; description: string; status: RoadmapStatus; category: string; eta: string | null };
export const fetchRoadmap = (tenant_id: string) => get<RoadmapView[]>(`/v1/roadmap${qs({ tenant_id })}`);
export const fetchPublicRoadmap = () => get<RoadmapItem[]>("/v1/public/roadmap");
export const voteRoadmap = (tenant_id: string, id: string) => post<RoadmapView>(`/v1/roadmap/${id}/vote${qs({ tenant_id })}`);
export const fetchAdminRoadmap = () => get<RoadmapItem[]>("/v1/admin/roadmap");
export const createRoadmapItem = (body: RoadmapItemIn) => request<RoadmapItem>("/v1/admin/roadmap", { method: "POST", body: JSON.stringify(body) });
export const updateRoadmapItem = (id: string, body: RoadmapItemIn) => put<RoadmapItem>(`/v1/admin/roadmap/${id}`, body);
export const deleteRoadmapItem = (id: string) => del(`/v1/admin/roadmap/${id}`);

export type FeedbackKind = "idea" | "bug" | "praise" | "other";
export type Feedback = { id: string; tenant_id: string; author: string; kind: FeedbackKind; text: string; page: string | null; roadmap_item_id: string | null; status: "new" | "reviewed" | "planned" | "closed"; created_at: string };
export const submitFeedback = (tenant_id: string, body: { kind: FeedbackKind; text: string; page?: string | null; roadmap_item_id?: string | null }) =>
  request<Feedback>(`/v1/feedback${qs({ tenant_id })}`, { method: "POST", body: JSON.stringify(body) });
export const fetchAdminFeedback = () => get<Feedback[]>("/v1/admin/feedback");
export const setFeedbackStatus = (id: string, status: Feedback["status"]) => patch<Feedback>(`/v1/admin/feedback/${id}`, { status });

export type DraftField = "description" | "services" | "persona_extra" | "instructions" | "greeting" | "faq_answer" | "rule" | "sms_template";
export type Draft = { field: DraftField; text: string; source: "llm" | "template"; website_used: string | null; notes: string[] };
export type Voice = { provider: "cartesia" | "elevenlabs"; id: string; name: string; gender: "female" | "male"; accent: string; description: string; recommended: boolean };
export type VoiceCatalogue = { voices: Voice[]; preview_available: Record<string, boolean> };
export const fetchVoices = () => get<VoiceCatalogue>("/v1/voices");
export type VoicePreview = { provider: string; voice_id: string; mime: string; audio_b64: string };
export const previewVoice = (body: { provider: string; voice_id: string; text?: string; speed?: number | null }) =>
  request<VoicePreview>("/v1/voices/preview", { method: "POST", body: JSON.stringify(body) });

export const requestDraft = (assistantId: string, body: { field: DraftField; brief: string; current?: string; website?: string | null; context?: string | null }) =>
  request<Draft>(`/v1/assistants/${assistantId}/draft`, { method: "POST", body: JSON.stringify(body) });
