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
export const fetchContacts = (params: { tenant_id?: string; q?: string } = {}) =>
  get<Contact[]>(`/v1/contacts${qs(params)}`);
export const fetchContact = (id: string) => get<Contact>(`/v1/contacts/${id}`);
export const fetchMembers = (tenant_id: string) => get<Member[]>(`/v1/organisations/${tenant_id}/members`);
export const fetchShared = (token: string) => get<SharedCall>(`/v1/public/share/${token}`);

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
