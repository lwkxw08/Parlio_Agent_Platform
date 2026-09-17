"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type Branding,
  type BrandingView,
  type ClientSummary,
  type CompliancePack,
  type SecurityPolicy,
  type SecurityView,
  type SsoConfig,
  type TenantLink,
  del,
  downloadCsv,
  fetchClients,
  fetchWhiteLabel,
  patch,
  put,
  request,
  when,
} from "@/lib/api";
import { humanize } from "@/app/breakdown";

const TABS = [["brand", "White-label"], ["clients", "Client accounts"], ["compliance", "Compliance pack"], ["security", "Security"]] as const;
type Tab = (typeof TABS)[number][0];

type Props = {
  tenant: string; initialTab?: string; canManage: boolean; isOwner: boolean; whitelabel: BrandingView; clients: ClientSummary[];
  pack: CompliancePack | null; security: SecurityView | null; securityError: string | null; mfaRequired: boolean;
};

export default function Settings(p: Props) {
  const [tab, setTab] = useState<Tab>((TABS.find(([id]) => id === p.initialTab)?.[0] ?? "brand") as Tab);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 6000); };
  return (
    <>
      <div className="tabs">
        {TABS.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      {tab === "brand" && <Brand tenant={p.tenant} canManage={p.canManage} view={p.whitelabel} flash={flash} />}
      {tab === "clients" && <Clients tenant={p.tenant} canManage={p.canManage} view={p.whitelabel} initial={p.clients} flash={flash} />}
      {tab === "compliance" && <Compliance tenant={p.tenant} pack={p.pack} />}
      {tab === "security" && <Security tenant={p.tenant} isOwner={p.isOwner} view={p.security} error={p.securityError} mfaRequired={p.mfaRequired} flash={flash} />}
    </>
  );
}

function Brand({ tenant, canManage, view, flash }: { tenant: string; canManage: boolean; view: BrandingView; flash: (m: string) => void }) {
  const [v, setV] = useState(view);
  const [b, setB] = useState<Branding>(view.branding);
  const [txt, setTxt] = useState("");
  const q = `?tenant_id=${tenant}`;
  const refresh = async () => { const r = await fetchWhiteLabel(tenant); if (r) { setV(r); setB(r.branding); } };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<Branding>(`/v1/whitelabel${q}`, {
      brand_name: b.brand_name, logo_url: b.logo_url || null, icon_url: b.icon_url || null, primary_colour: b.primary_colour, accent_colour: b.accent_colour,
      support_email: b.support_email || null, support_url: b.support_url || null, hide_powered_by: b.hide_powered_by, custom_domain: b.custom_domain?.trim().toLowerCase() || null,
    });
    if (!r.ok) return flash(`Could not save: ${r.error}`);
    flash("Branding saved."); await refresh();
  };
  const verify = async () => {
    const r = await request<Branding>(`/v1/whitelabel/verify-domain${q}`, { method: "POST", body: JSON.stringify({ txt_records: txt.split(/[\n,]/).map((s) => s.trim()).filter(Boolean) }) });
    if (!r.ok) return flash(`Could not verify: ${r.error}`);
    flash(r.data.domain_verified ? "Domain verified." : "TXT record does not match yet — DNS can take up to an hour to propagate.");
    await refresh();
  };
  return (
    <div className="two-col">
      <form className="section form" onSubmit={save}>
        <h2>Branding</h2>
        {v.parent && <p className="hint">This account is managed by an agency ({v.parent.parent_tenant_id}){v.parent.inherit_branding ? " and inherits its branding on the public chat widget and emails" : ""}.</p>}
        <div className="row">
          <label>Brand name<input value={b.brand_name} required disabled={!canManage} onChange={(e) => setB({ ...b, brand_name: e.target.value })} /></label>
          <label className="check" style={{ alignSelf: "end" }}><input type="checkbox" checked={b.hide_powered_by} disabled={!canManage} onChange={(e) => setB({ ...b, hide_powered_by: e.target.checked })} /> Hide &ldquo;Powered by ParlioTec&rdquo;</label>
        </div>
        <div className="row">
          <label>Logo URL<input value={b.logo_url ?? ""} disabled={!canManage} onChange={(e) => setB({ ...b, logo_url: e.target.value })} placeholder="https://…/logo.png" /></label>
          <label>Icon URL<input value={b.icon_url ?? ""} disabled={!canManage} onChange={(e) => setB({ ...b, icon_url: e.target.value })} placeholder="https://…/icon.png" /></label>
        </div>
        <div className="row">
          <label>Primary colour<span style={{ display: "flex", gap: 8 }}><input type="color" value={b.primary_colour} disabled={!canManage} onChange={(e) => setB({ ...b, primary_colour: e.target.value })} style={{ width: 48, padding: 2 }} /><input value={b.primary_colour} disabled={!canManage} onChange={(e) => setB({ ...b, primary_colour: e.target.value })} /></span></label>
          <label>Accent colour<span style={{ display: "flex", gap: 8 }}><input type="color" value={b.accent_colour} disabled={!canManage} onChange={(e) => setB({ ...b, accent_colour: e.target.value })} style={{ width: 48, padding: 2 }} /><input value={b.accent_colour} disabled={!canManage} onChange={(e) => setB({ ...b, accent_colour: e.target.value })} /></span></label>
        </div>
        <div className="row">
          <label>Support email<input type="email" value={b.support_email ?? ""} disabled={!canManage} onChange={(e) => setB({ ...b, support_email: e.target.value })} /></label>
          <label>Support URL<input value={b.support_url ?? ""} disabled={!canManage} onChange={(e) => setB({ ...b, support_url: e.target.value })} /></label>
        </div>
        <label>Custom domain for the dashboard &amp; chat widget<input value={b.custom_domain ?? ""} disabled={!canManage} onChange={(e) => setB({ ...b, custom_domain: e.target.value })} placeholder="app.yourbrand.co.uk" /></label>
        {canManage && <button className="primary" type="submit">Save</button>}
        <p className="hint">Branding applies to the public chat widget, payment/approval pages and outgoing emails/SMS sign-offs.</p>
      </form>
      <div className="section">
        <h2>Custom domain</h2>
        {!v.domain ? <p className="muted small">Enter a domain on the left and save to get DNS instructions.</p> : (
          <>
            <p className="small"><span className={`pill ${v.domain.verified ? "ok" : "warn"}`}>{v.domain.verified ? "verified" : "pending verification"}</span> {v.domain.domain}</p>
            <p className="hint">Add these records at your DNS provider, then paste the TXT value(s) you published below and click Verify.</p>
            <table className="small">
              <thead><tr><th>Type</th><th>Name</th><th>Value</th></tr></thead>
              <tbody>
                <tr><td>CNAME</td><td>{v.domain.domain}</td><td><code>{v.domain.cname_target}</code></td></tr>
                <tr><td>TXT</td><td><code>{v.domain.txt_name}</code></td><td><code>{v.domain.txt_value}</code></td></tr>
              </tbody>
            </table>
            {canManage && !v.domain.verified && (
              <div className="form" style={{ marginTop: "0.6rem" }}>
                <label>Published TXT value(s)<textarea rows={2} value={txt} onChange={(e) => setTxt(e.target.value)} placeholder={v.domain.txt_value} /></label>
                <button onClick={verify}>Verify domain</button>
              </div>
            )}
            <p className="hint">Verification proves you control the domain. Serving the dashboard on it also needs the domain added to the Cloudflare project — ask support once verified.</p>
          </>
        )}
      </div>
    </div>
  );
}

function Clients({ tenant, canManage, view, initial, flash }: { tenant: string; canManage: boolean; view: BrandingView; initial: ClientSummary[]; flash: (m: string) => void }) {
  const [rows, setRows] = useState(initial);
  const [form, setForm] = useState({ client_name: "", business_name: "", inherit_branding: true });
  const q = `?tenant_id=${tenant}`;
  const refresh = async () => { const r = await fetchClients(tenant); if (r) setRows(r); };
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await request<TenantLink>(`/v1/whitelabel/clients${q}`, { method: "POST", body: JSON.stringify(form) });
    if (!r.ok) return flash(`Could not create: ${r.error}`);
    setForm({ client_name: "", business_name: "", inherit_branding: true }); flash(`Client organisation ${r.data.child_tenant_id} created — your admins can manage it; invite their staff from Team.`); await refresh();
  };
  const toggle = async (l: TenantLink) => { if (await patch<TenantLink>(`/v1/whitelabel/clients/${l.id}${q}`, { inherit_branding: !l.inherit_branding })) await refresh(); };
  return (
    <div className="two-col">
      <div className="section">
        <h2>Client accounts {view.is_agency && <span className="pill ok">agency</span>}</h2>
        <p className="hint">
          Agency / reseller mode: each client is its own organisation with separate calls, data, users and billing. Your admins and owners are added as admins so you can
          manage it, and can switch to it from the organisation chips at the top of each page.
        </p>
        {rows.length === 0 ? <p className="muted small">No client organisations yet.</p> : (
          <table>
            <thead><tr><th>Client</th><th>Organisation</th><th>Members</th><th>Assistants</th><th>Plan / usage</th><th>Branding</th><th></th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.link.id}>
                  <td>{c.link.client_name}</td><td className="small">{c.link.child_tenant_id}</td><td>{c.members}</td><td>{c.assistants}</td>
                  <td className="small">{c.usage ? `${c.usage.plan.name} · ${Math.round(c.usage.minutes_used)} min` : "—"}</td>
                  <td><span className={`pill ${c.link.inherit_branding ? "" : "warn"}`}>{c.link.inherit_branding ? "inherits yours" : "own"}</span></td>
                  <td style={{ display: "flex", gap: 4 }}>
                    <Link className="small" href={`/?tenant=${c.link.child_tenant_id}`}>Open</Link>
                    <Link className="small" href={`/billing?tenant=${c.link.child_tenant_id}`}>Billing</Link>
                    {canManage && <button className="small" onClick={() => toggle(c.link)}>{c.link.inherit_branding ? "Use own branding" : "Inherit branding"}</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {canManage && (
        <form className="section form" onSubmit={create}>
          <h2>Add a client</h2>
          <label>Client name (your label)<input value={form.client_name} required onChange={(e) => setForm({ ...form, client_name: e.target.value })} placeholder="Smith Plumbing" /></label>
          <label>Their business name (used by the assistant)<input value={form.business_name} required onChange={(e) => setForm({ ...form, business_name: e.target.value })} placeholder="Smith Plumbing & Heating Ltd" /></label>
          <label className="check"><input type="checkbox" checked={form.inherit_branding} onChange={(e) => setForm({ ...form, inherit_branding: e.target.checked })} /> Use my branding for this client</label>
          <button className="primary" type="submit">Create client organisation</button>
        </form>
      )}
    </div>
  );
}

function Compliance({ tenant, pack }: { tenant: string; pack: CompliancePack | null }) {
  if (!pack) return <p className="muted">Compliance pack unavailable.</p>;
  const consent = Object.entries(pack.consent);
  return (
    <>
      <div className="section">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
          <h2>Compliance pack</h2>
          <span style={{ display: "flex", gap: 8 }}>
            <button className="primary" onClick={async () => { const err = await downloadCsv("audit", tenant); if (err) alert(`Export failed: ${err}`); }}>Download audit log (CSV)</button>
            <Link className="btn" href={`/compliance?tenant=${tenant}`}>Retention &amp; GDPR tools</Link>
          </span>
        </div>
        <p className="hint">A single view of what you can hand to a customer's DPO or auditor: where data lives, which vendors process it, consent, retention and access controls. Generated {when(pack.generated_at)}.</p>
        <dl className="kv">
          <dt>Data residency</dt><dd>{pack.data_residency}</dd>
          <dt>Retention</dt><dd>Recordings {pack.retention.recording_days} days · transcripts {pack.retention.transcript_days} days · calls {pack.retention.call_days} days · redaction on write {pack.retention.redact_on_write ? "on" : "off"}</dd>
          <dt>Access controls</dt><dd>2FA: {pack.security.require_2fa} · SSO: {pack.security.sso.provider} · sessions {pack.security.session_hours} h</dd>
          <dt>Audit entries</dt><dd>{pack.audit_entries}</dd>
        </dl>
        {pack.notes.length > 0 && <ul className="small" style={{ margin: "0.4rem 0 0 1rem" }}>{pack.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      </div>
      <div className="two-col">
        <div className="section">
          <h2>Assistants &amp; region pinning</h2>
          <table className="small">
            <thead><tr><th>Assistant</th><th>Region profile</th><th>STT</th><th>LLM</th><th>TTS</th><th>Consent announcement</th><th>Recording</th></tr></thead>
            <tbody>
              {pack.assistants.map((a) => (
                <tr key={a.assistant_id}>
                  <td>{a.name}</td><td><span className={`pill ${a.region_profile.startsWith("sovereign") ? "ok" : ""}`}>{humanize(a.region_profile)}</span></td>
                  <td>{a.stt.join(" → ")}</td><td>{a.llm.join(" → ")}</td><td>{a.tts.join(" → ")}</td>
                  <td>{a.consent_announcement ? "yes" : "no"}</td><td>{a.recording_enabled ? "on" : "off"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint">Region profile is set per assistant in Studio. Sovereign profiles pin storage and telephony to the UK; UK-only model providers are a configuration seam and are not yet operational.</p>
        </div>
        <div className="section">
          <h2>Sub-processors</h2>
          <table className="small">
            <thead><tr><th>Vendor</th><th>Purpose</th><th>Region</th><th>DPA</th></tr></thead>
            <tbody>{pack.sub_processors.map((s, i) => <tr key={i}><td>{s.name}</td><td>{s.purpose}</td><td>{s.region}</td><td>{s.dpa ?? ""}</td></tr>)}</tbody>
          </table>
          {consent.length > 0 && (
            <>
              <h2 style={{ marginTop: "1rem" }}>Consent</h2>
              <dl className="kv">{consent.map(([k, v]) => <><dt key={`${k}-t`}>{k.replace(/_/g, " ")}</dt><dd key={`${k}-d`}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></>)}</dl>
            </>
          )}
        </div>
      </div>
    </>
  );
}

const PROVIDERS: [SsoConfig["provider"], string][] = [
  ["none", "None"], ["microsoft_entra", "Microsoft Entra ID (Azure AD)"], ["google_workspace", "Google Workspace"], ["okta", "Okta"], ["saml", "Other SAML 2.0"], ["oidc", "Other OIDC"],
];

function Security({ tenant, isOwner, view, error, mfaRequired, flash }: { tenant: string; isOwner: boolean; view: SecurityView | null; error: string | null; mfaRequired: boolean; flash: (m: string) => void }) {
  const [v, setV] = useState(view);
  const [pol, setPol] = useState<SecurityPolicy | null>(view?.policy ?? null);
  const [token, setToken] = useState<string | null>(null);
  const q = `?tenant_id=${tenant}`;
  if (!v || !pol) {
    return (
      <div className="section">
        <h2>Security</h2>
        {mfaRequired
          ? <p className="hint">This organisation requires two-factor authentication. <Link href="/account">Set up or verify 2FA on your account</Link>, then come back.</p>
          : <p className="muted">{error ?? "Unavailable"}</p>}
      </div>
    );
  }
  const refresh = async () => { const r = await request<SecurityView>(`/v1/security${q}`); if (r.ok) { setV(r.data); setPol(r.data.policy); } };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<SecurityPolicy>(`/v1/security${q}`, { require_2fa: pol.require_2fa, session_hours: pol.session_hours, sso: pol.sso });
    if (!r.ok) return flash(`Could not save: ${r.error}`);
    flash("Security policy saved."); await refresh();
  };
  const issue = async () => {
    const r = await request<{ token: string }>(`/v1/security/scim/token${q}`, { method: "POST" });
    if (!r.ok) return flash(`Could not issue token: ${r.error}`);
    setToken(r.data.token); await refresh();
  };
  const revoke = async () => { if (await del(`/v1/security/scim/token${q}`)) { setToken(null); flash("SCIM token revoked."); await refresh(); } };
  const sso = pol.sso;
  const setSso = (patchSso: Partial<SsoConfig>) => setPol({ ...pol, sso: { ...sso, ...patchSso } });
  const statusPill = v.sso_status === "live" ? "ok" : v.sso_status === "configured_not_live" ? "warn" : "";
  return (
    <div className="two-col">
      <form className="section form" onSubmit={save}>
        <h2>Two-factor authentication</h2>
        <label>Require 2FA for
          <select value={pol.require_2fa} disabled={!isOwner} onChange={(e) => setPol({ ...pol, require_2fa: e.target.value as SecurityPolicy["require_2fa"] })}>
            <option value="off">nobody (optional)</option><option value="admins">owners &amp; admins</option><option value="all">everyone</option>
          </select>
        </label>
        <label>Sign-in session length (hours)<input type="number" min={1} max={720} value={pol.session_hours} disabled={!isOwner} onChange={(e) => setPol({ ...pol, session_hours: Number(e.target.value) })} /></label>
        {v.members_without_2fa.length > 0 && pol.require_2fa !== "off" && (
          <p className="small" style={{ color: "var(--warn-fg, inherit)" }}>{v.members_without_2fa.length} member{v.members_without_2fa.length === 1 ? "" : "s"} affected by the policy have not enrolled yet: {v.members_without_2fa.join(", ")}. They will be asked to set up an authenticator on their next visit.</p>
        )}
        <p className="hint">Each user enrols an authenticator app on their <Link href="/account">Account</Link> page, with recovery codes. Enforcement applies to this organisation only.</p>

        <h2 style={{ marginTop: "1rem" }}>Single sign-on <span className={`pill ${statusPill}`}>{v.sso_status.replace(/_/g, " ")}</span></h2>
        <label>Identity provider
          <select value={sso.provider} disabled={!isOwner} onChange={(e) => setSso({ provider: e.target.value as SsoConfig["provider"], protocol: e.target.value === "saml" ? "saml" : "oidc" })}>
            {PROVIDERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        {sso.provider !== "none" && (
          <>
            <label>Email domains (comma-separated)<input value={sso.domains.join(", ")} disabled={!isOwner} onChange={(e) => setSso({ domains: e.target.value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean) })} placeholder="yourbusiness.co.uk" /></label>
            <div className="row">
              <label>Issuer / entity ID<input value={sso.issuer ?? ""} disabled={!isOwner} onChange={(e) => setSso({ issuer: e.target.value || null })} /></label>
              <label>Client ID<input value={sso.client_id ?? ""} disabled={!isOwner} onChange={(e) => setSso({ client_id: e.target.value || null })} /></label>
            </div>
            <label>Metadata URL (OIDC discovery or SAML metadata)<input value={sso.metadata_url ?? ""} disabled={!isOwner} onChange={(e) => setSso({ metadata_url: e.target.value || null })} /></label>
            <label className="check"><input type="checkbox" checked={sso.enforce} disabled={!isOwner || v.sso_status !== "live"} onChange={(e) => setSso({ enforce: e.target.checked })} /> Require SSO for these domains (available once live)</label>
            <p className="hint">
              {v.sso_status === "live"
                ? "Users on these domains can sign in with your identity provider."
                : "Saving records the configuration for your provider. Sign-in via the provider goes live once ParlioTec connects it on the auth layer (a support step) — until then, users continue to sign in with email or Google."}
            </p>
          </>
        )}
        {isOwner && <button className="primary" type="submit">Save policy</button>}
      </form>
      <div className="section">
        <h2>SCIM user provisioning <span className={`pill ${pol.scim.enabled ? "ok" : ""}`}>{pol.scim.enabled ? "enabled" : "off"}</span></h2>
        <p className="hint">Let your identity provider (Entra, Okta, Google) create, update and deactivate ParlioTec users automatically. Enterprise / Sovereign tiers.</p>
        <dl className="kv">
          <dt>SCIM base URL</dt><dd><code>{v.scim_endpoint}</code></dd>
          <dt>Default role</dt><dd>{pol.scim.default_role}</dd>
          <dt>Token</dt><dd>{pol.scim.token_hint ? <>{pol.scim.token_hint} <span className="small muted">issued {pol.scim.issued_at ? when(pol.scim.issued_at) : ""}</span></> : "none issued"}</dd>
        </dl>
        {token && (
          <div className="card" style={{ margin: "0.6rem 0" }}>
            <p className="small">Copy this bearer token into your IdP now — it is shown once:</p>
            <code style={{ wordBreak: "break-all" }}>{token}</code>
          </div>
        )}
        {isOwner && (
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={issue}>{pol.scim.enabled ? "Rotate token" : "Enable & issue token"}</button>
            {pol.scim.enabled && <button onClick={revoke}>Disable SCIM</button>}
          </div>
        )}
      </div>
    </div>
  );
}
