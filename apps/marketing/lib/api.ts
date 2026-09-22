export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const DASHBOARD_URL = (process.env.NEXT_PUBLIC_DASHBOARD_URL ?? "https://app.parliotec.com").replace(/\/$/, "");
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://parliotec.com").replace(/\/$/, "");

export type PublicPlan = {
  id: string;
  name: string;
  monthly_pence: number;
  included_minutes: number;
  overage_pence_per_minute: number;
  included_sms: number;
  sms_overage_pence: number;
  included_numbers: number;
  max_assistants: number;
  max_concurrent_calls: number;
  max_resources: number;
  max_sites: number;
  max_members: number;
  features: string[];
  entitlements: string[];
  enterprise: boolean;
  trial_days: number;
};

export type DemoInfo = { voice_available: boolean; phone: string | null; minutes_remaining: number };

export type SiteInfo = { plans: PublicPlan[]; trial_days: number; demo: DemoInfo; dashboard_url: string };

export type VoiceSession = {
  call_id: string;
  room: string;
  identity: string;
  url: string | null;
  token: string;
  simulated: boolean;
};

/** `build` = fetched once at export time (server components); default = always fresh (client). */
export async function fetchSiteInfo(mode: "live" | "build" = "live"): Promise<SiteInfo | null> {
  try {
    const r = await fetch(`${API_URL}/v1/public/site`, mode === "build" ? { cache: "force-cache" } : { cache: "no-store" });
    if (!r.ok) return null;
    return (await r.json()) as SiteInfo;
  } catch {
    return null;
  }
}

export async function startDemoVoice(
  visitor: string,
  name: string | undefined,
  pageUrl: string,
): Promise<{ session: VoiceSession | null; error: string | null }> {
  try {
    const r = await fetch(`${API_URL}/v1/public/site/demo/voice`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ visitor, name, page_url: pageUrl }),
    });
    if (!r.ok) {
      const detail = (await r.json().catch(() => null)) as { detail?: string } | null;
      return { session: null, error: detail?.detail ?? `demo unavailable (${r.status})` };
    }
    return { session: (await r.json()) as VoiceSession, error: null };
  } catch {
    return { session: null, error: "could not reach the demo service" };
  }
}

export type ContactIn = {
  name: string;
  email: string;
  company?: string;
  phone?: string;
  interest?: string;
  message: string;
  website?: string;
};

export async function sendContact(body: ContactIn): Promise<boolean> {
  try {
    const r = await fetch(`${API_URL}/v1/public/site/contact`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.ok;
  } catch {
    return false;
  }
}

export const signupUrl = `${DASHBOARD_URL}/login`;
export const loginUrl = `${DASHBOARD_URL}/login`;

export function gbp(pence: number): string {
  return `£${(pence / 100).toLocaleString("en-GB", { maximumFractionDigits: pence % 100 === 0 ? 0 : 2 })}`;
}

export function cap(n: number, unit: string): string {
  return n === 0 ? `Unlimited ${unit}` : `${n.toLocaleString("en-GB")} ${unit}`;
}
