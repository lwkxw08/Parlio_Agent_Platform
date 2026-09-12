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

export const ms = (s: number | null | undefined) =>
  s == null ? "—" : `${Math.round(s * 1000)} ms`;
