"use client";

import { useState } from "react";
import {
  MFA_COOKIE,
  type MfaToken,
  type TwoFactorStatus,
  type UserSession,
  del,
  fetchSessions,
  fetchTwoFactor,
  post,
  request,
  when,
} from "@/lib/api";

type Enrol = { secret: string; otpauth_uri: string };

function setMfaCookie(token: string | null, expiresAt?: string) {
  const maxAge = expiresAt ? Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000)) : 0;
  document.cookie = token
    ? `${MFA_COOKIE}=${encodeURIComponent(token)}; path=/; max-age=${maxAge}; samesite=lax`
    : `${MFA_COOKIE}=; path=/; max-age=0`;
}

export default function AccountSecurity({ status, sessions }: { status: TwoFactorStatus | null; sessions: UserSession[] }) {
  const [st, setSt] = useState(status);
  const [ss, setSs] = useState(sessions);
  const [enrol, setEnrol] = useState<Enrol | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 6000); };
  const refresh = async () => { const [a, b] = await Promise.all([fetchTwoFactor(), fetchSessions()]); if (a) setSt(a); if (b) setSs(b); };
  if (!st) return <p className="muted small">Two-factor status unavailable.</p>;

  const start = async () => { const r = await post<Enrol>("/v1/account/2fa/enrol"); if (r) setEnrol(r); else flash("Could not start enrolment"); };
  const confirm = async () => {
    const r = await request<{ codes: string[] }>("/v1/account/2fa/confirm", { method: "POST", body: JSON.stringify({ code }) });
    if (!r.ok) return flash(`Code not accepted: ${r.error}`);
    setCodes(r.data.codes); setEnrol(null); setCode(""); flash("Two-factor authentication is on."); await refresh();
  };
  const verify = async () => {
    const r = await request<MfaToken>("/v1/account/2fa/verify", { method: "POST", body: JSON.stringify({ code }) });
    if (!r.ok) return flash(`Code not accepted: ${r.error}`);
    setMfaCookie(r.data.token, r.data.expires_at); setCode(""); flash("Verified for this device."); await refresh(); window.location.reload();
  };
  const regen = async () => {
    const r = await request<{ codes: string[] }>("/v1/account/2fa/recovery-codes", { method: "POST", body: JSON.stringify({ code }) });
    if (!r.ok) return flash(`Code not accepted: ${r.error}`);
    setCodes(r.data.codes); setCode(""); await refresh();
  };
  const disable = async () => {
    const r = await request<null>("/v1/account/2fa/disable", { method: "POST", body: JSON.stringify({ code }) });
    if (!r.ok) return flash(`Code not accepted: ${r.error}`);
    setMfaCookie(null); setCode(""); setCodes(null); flash("Two-factor authentication turned off."); await refresh();
  };
  const revoke = async (id: string) => { if (await del(`/v1/account/sessions/${id}`)) await refresh(); };
  const revokeOthers = async () => { const r = await post<{ revoked: number }>("/v1/account/sessions/revoke-others"); flash(r ? `${r.revoked} other session${r.revoked === 1 ? "" : "s"} signed out.` : "Failed"); await refresh(); };
  const codeInput = <input value={code} inputMode="numeric" autoComplete="one-time-code" onChange={(e) => setCode(e.target.value.trim())} placeholder="123 456 or recovery code" style={{ maxWidth: 220 }} />;

  return (
    <>
      <div className="section form">
        <h2>Two-factor authentication <span className={`pill ${st.confirmed ? "ok" : ""}`}>{st.confirmed ? (st.mfa_verified ? "on · verified here" : "on · not verified on this device") : "off"}</span></h2>
        {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
        {!st.confirmed && !enrol && (
          <>
            <p className="hint">Protect your account with an authenticator app (Microsoft Authenticator, Google Authenticator, 1Password…). Organisations can require this for their admins or all members.</p>
            <button className="primary" onClick={start}>Set up authenticator</button>
          </>
        )}
        {enrol && (
          <>
            <p className="small">1. Add this key to your authenticator app (choose &ldquo;enter a setup key&rdquo;), or <a href={enrol.otpauth_uri}>open in your authenticator</a> on this device:</p>
            <code style={{ wordBreak: "break-all", fontSize: "1.05rem", letterSpacing: 1 }}>{enrol.secret.replace(/(.{4})/g, "$1 ").trim()}</code>
            <p className="small muted">Account: ParlioTec · Time-based (TOTP), 6 digits, 30 s.</p>
            <p className="small">2. Enter the 6-digit code it shows:</p>
            <div style={{ display: "flex", gap: 8 }}>{codeInput}<button className="primary" onClick={confirm} disabled={code.length < 6}>Confirm</button><button onClick={() => setEnrol(null)}>Cancel</button></div>
          </>
        )}
        {st.confirmed && !enrol && (
          <>
            <p className="hint">{st.recovery_codes_left} recovery code{st.recovery_codes_left === 1 ? "" : "s"} left. Enter a code from your app to verify this device, regenerate recovery codes or turn 2FA off.</p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {codeInput}
              {!st.mfa_verified && <button className="primary" onClick={verify} disabled={code.length < 6}>Verify this device</button>}
              <button onClick={regen} disabled={code.length < 6}>New recovery codes</button>
              <button className="danger" onClick={disable} disabled={code.length < 6}>Turn off 2FA</button>
            </div>
          </>
        )}
        {codes && (
          <div className="card" style={{ marginTop: "0.6rem" }}>
            <p className="small"><strong>Recovery codes</strong> — store these somewhere safe; each works once if you lose your authenticator. They are shown only now.</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 4 }}>{codes.map((c) => <code key={c}>{c}</code>)}</div>
            <button className="small" style={{ marginTop: 6 }} onClick={() => setCodes(null)}>I&apos;ve saved them</button>
          </div>
        )}
      </div>
      <div className="section">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2>Devices &amp; sessions</h2>
          {ss.some((s) => !s.current && !s.revoked_at) && <button onClick={revokeOthers}>Sign out other devices</button>}
        </div>
        {ss.length === 0 ? <p className="muted small">Sessions are recorded when you verify two-factor authentication on a device.</p> : (
          <table>
            <thead><tr><th>Device</th><th>IP</th><th>Started</th><th>Last seen</th><th>Expires</th><th></th></tr></thead>
            <tbody>
              {ss.map((s) => (
                <tr key={s.id}>
                  <td>{s.label} {s.current && <span className="pill ok">this device</span>} {s.mfa && <span className="pill">2FA</span>}</td>
                  <td className="small muted">{s.ip ?? "—"}</td><td className="small">{when(s.created_at)}</td><td className="small">{when(s.last_seen_at)}</td>
                  <td className="small">{s.revoked_at ? <span className="pill bad">revoked</span> : when(s.expires_at)}</td>
                  <td>{!s.revoked_at && <button className="small" onClick={() => revoke(s.id)}>{s.current ? "Sign out" : "Revoke"}</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
