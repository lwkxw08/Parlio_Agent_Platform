"use client";

import { SUPABASE_ANON_KEY, SUPABASE_URL, TOKEN_COOKIE } from "@/lib/api";

/** Supabase Auth (GoTrue) via its REST API — no SDK needed for email/password + Google OAuth. */
export const supabaseConfigured = () => Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

export function setToken(token: string | null, maxAgeS = 3600) {
  document.cookie = token
    ? `${TOKEN_COOKIE}=${encodeURIComponent(token)}; path=/; max-age=${maxAgeS}; samesite=lax`
    : `${TOKEN_COOKIE}=; path=/; max-age=0`;
}

type Session = { access_token: string; expires_in: number; user: { email: string } };

export async function signInWithPassword(email: string, password: string): Promise<string | null> {
  const res = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { "content-type": "application/json", apikey: SUPABASE_ANON_KEY },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const b = (await res.json().catch(() => ({}))) as { error_description?: string; msg?: string };
    return b.error_description ?? b.msg ?? "sign-in failed";
  }
  const s = (await res.json()) as Session;
  setToken(s.access_token, s.expires_in);
  return null;
}

export type SignUpResult = { error: string } | { signedIn: boolean };

export async function signUp(email: string, password: string): Promise<SignUpResult> {
  const res = await fetch(`${SUPABASE_URL}/auth/v1/signup`, {
    method: "POST",
    headers: { "content-type": "application/json", apikey: SUPABASE_ANON_KEY },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const b = (await res.json().catch(() => ({}))) as { msg?: string };
    return { error: b.msg ?? "sign-up failed" };
  }
  const s = (await res.json()) as Partial<Session>;
  if (s.access_token) setToken(s.access_token, s.expires_in);
  return { signedIn: !!s.access_token };
}

/** Re-send the sign-up confirmation email. */
export async function resendConfirmation(email: string): Promise<string | null> {
  const res = await fetch(`${SUPABASE_URL}/auth/v1/resend`, {
    method: "POST",
    headers: { "content-type": "application/json", apikey: SUPABASE_ANON_KEY },
    body: JSON.stringify({ type: "signup", email }),
  });
  return res.ok ? null : "could not resend the email - try again in a minute";
}

export function signInWithGoogle() {
  const redirect = encodeURIComponent(`${window.location.origin}/login`);
  window.location.href = `${SUPABASE_URL}/auth/v1/authorize?provider=google&redirect_to=${redirect}`;
}

/** After an OAuth redirect Supabase puts the session in the URL fragment. */
export function captureOAuthRedirect(): boolean {
  const h = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const token = h.get("access_token");
  if (!token) return false;
  setToken(token, Number(h.get("expires_in") ?? 3600));
  window.history.replaceState(null, "", window.location.pathname);
  return true;
}

export async function sendPasswordReset(email: string): Promise<string | null> {
  const res = await fetch(`${SUPABASE_URL}/auth/v1/recover`, {
    method: "POST",
    headers: { "content-type": "application/json", apikey: SUPABASE_ANON_KEY },
    body: JSON.stringify({ email }),
  });
  return res.ok ? null : "could not send reset email";
}

export function signOut() {
  setToken(null);
  window.location.href = "/login";
}
