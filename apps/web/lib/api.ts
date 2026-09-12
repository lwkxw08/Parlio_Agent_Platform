export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type CallRecord = {
  call_id: string;
  tenant_id: string;
  assistant_id: string;
  caller: string | null;
  dialed: string | null;
  status: string;
  started_at: string;
  answered_at: string | null;
  ended_at: string | null;
  answer_latency_s: number | null;
  duration_s: number | null;
  latency: { turns?: number; p50_s?: number; p95_s?: number };
  transcript: { role: string; text: string; interrupted?: boolean }[];
  recordings: string[];
  end_reason: string | null;
  transfers: { transfer_id: string; destination: string; department: string | null; mode: string; outcome: string; at: string }[];
  ticket_ids: string[];
  escalated: boolean;
  escalation_keyword: string | null;
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

export type HandoffAnalytics = {
  transfers: {
    total: number;
    by_outcome: Record<string, number>;
    by_department: Record<string, number>;
    by_destination: Record<string, number>;
    answer_rate: number | null;
  };
  tickets: {
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
};

export type Assistant = {
  assistant_id: string;
  name: string;
  business_name: string;
  language: string;
  region_profile: string;
  greeting: string;
};

async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export const fetchHealth = () => get<{ status: string; env: string }>("/healthz");
export const fetchCalls = () => get<CallRecord[]>("/v1/calls");
export const fetchCall = (id: string) => get<CallRecord>(`/v1/calls/${id}`);
export const fetchAssistants = () => get<Assistant[]>("/v1/assistants");
export const fetchTickets = (status?: TicketStatus) =>
  get<Ticket[]>(`/v1/tickets${status ? `?status=${status}` : ""}`);
export const fetchTicket = (id: string) => get<TicketDetail>(`/v1/tickets/${id}`);
export const fetchTransfers = () => get<TransferRecord[]>("/v1/transfers");
export const fetchHandoffAnalytics = () => get<HandoffAnalytics>("/v1/analytics/handoff");

export async function post<T>(path: string, body: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export const secs = (s: number | null | undefined) => {
  if (s == null) return "—";
  const a = Math.abs(s);
  const txt = a < 90 ? `${Math.round(a)} s` : a < 5400 ? `${Math.round(a / 60)} min` : `${(a / 3600).toFixed(1)} h`;
  return s < 0 ? `-${txt}` : txt;
};

export const ms = (s: number | null | undefined) =>
  s == null ? "—" : `${Math.round(s * 1000)} ms`;
