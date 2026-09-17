"use client";

import { useState } from "react";
import {
  API_URL,
  type Connector,
  type ConnectorProvider,
  type ConnectorTrigger,
  type ProviderInfo,
  type SyncJob,
  type TenantApiKey,
  del,
  downloadCsv,
  post,
  request,
  when,
} from "@/lib/api";
import { humanize } from "@/app/breakdown";

const TRIGGERS: [ConnectorTrigger, string][] = [
  ["lead.qualified", "Qualified lead"],
  ["call.completed", "Every completed call"],
  ["ticket.created", "Ticket / callback"],
  ["booking.created", "Booking made"],
];
const CATEGORIES: [string, string][] = [
  ["crm", "CRM"], ["automation", "Automation & webhooks"], ["chat", "Team chat"], ["sheet", "Spreadsheets"],
  ["field_service", "Field service"], ["test", "Testing"],
];
const OPTION_LABELS: Record<string, [string, string]> = {
  spreadsheet_id: ["Spreadsheet ID", "from the sheet URL: /spreadsheets/d/<ID>/"],
  sheet: ["Sheet tab", "ParlioTec"],
  instance_url: ["Login / My Domain URL", "https://yourorg.my.salesforce.com"],
  client_id: ["Consumer key (client ID)", ""],
  client_secret: ["Client secret", ""],
  company_domain: ["Company domain", "yourcompany (from yourcompany.pipedrive.com)"],
  accounts_domain: ["Accounts domain", "accounts.zoho.eu"],
};

type Props = {
  tenant: string;
  canManage: boolean;
  providers: ProviderInfo[];
  payloadFields: string[];
  connectors: Connector[];
  jobs: SyncJob[];
  apiKeys: TenantApiKey[];
};

const pill = (s: string) => (
  <span className={`pill ${s === "sent" || s === "connected" ? "ok" : s === "failed" || s === "error" ? "bad" : s === "skipped" ? "" : "warn"}`}>{humanize(s)}</span>
);

export default function Connectors(p: Props) {
  const [connectors, setConnectors] = useState(p.connectors);
  const [jobs, setJobs] = useState(p.jobs);
  const [editing, setEditing] = useState<Connector | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const q = `?tenant_id=${p.tenant}`;
  const info = (id: ConnectorProvider) => p.providers.find((x) => x.provider === id);

  const refreshJobs = async () => {
    const r = await request<SyncJob[]>(`/v1/connectors/jobs${q}`);
    if (r.ok) setJobs(r.data);
  };
  const upsert = (c: Connector) => setConnectors((cs) => (cs.some((x) => x.id === c.id) ? cs.map((x) => (x.id === c.id ? c : x)) : [...cs, c]));
  const test = async (c: Connector) => {
    const r = await post<{ ok: boolean; detail: string; connector: Connector }>(`/v1/connectors/${c.id}/test${q}`);
    if (!r) return setMsg("Test failed");
    upsert(r.connector); setMsg(`${c.name}: ${r.ok ? "connected" : "failed"} — ${r.detail}`);
  };
  const sample = async (c: Connector) => {
    const r = await request<SyncJob>(`/v1/connectors/${c.id}/send-sample${q}`, { method: "POST" });
    if (!r.ok) return setMsg(`Sample not sent: ${r.error}`);
    setMsg(`Sample ${r.data.status}${r.data.external_ref ? ` → ${r.data.external_ref}` : ""}${r.data.error ? ` — ${r.data.error}` : ""}`);
    await refreshJobs();
    const cr = await request<Connector[]>(`/v1/connectors${q}`);
    if (cr.ok) setConnectors(cr.data);
  };
  const toggle = async (c: Connector) => {
    const r = await request<Connector>(`/v1/connectors/${c.id}${q}`, { method: "PATCH", body: JSON.stringify({ enabled: !c.enabled }) });
    if (r.ok) upsert(r.data);
  };
  const remove = async (c: Connector) => {
    if (!confirm(`Remove "${c.name}"? Past sync history is kept.`)) return;
    if (await del(`/v1/connectors/${c.id}${q}`)) setConnectors((cs) => cs.filter((x) => x.id !== c.id));
  };
  const retry = async (j: SyncJob) => {
    const r = await request<SyncJob>(`/v1/connectors/jobs/${j.id}/retry${q}`, { method: "POST" });
    if (!r.ok) return setMsg(`Retry failed: ${r.error}`);
    setJobs((js) => js.map((x) => (x.id === r.data.id ? r.data : x)));
  };
  const showSecret = async (c: Connector) => {
    const r = await request<{ signing_secret: string | null }>(`/v1/connectors/${c.id}/signing-secret${q}`);
    if (r.ok) prompt("Webhook signing secret (verify X-Parlio-Signature = sha256=HMAC(body)):", r.data.signing_secret ?? "");
  };

  return (
    <>
      <div className="section">
        <h2>Connected apps</h2>
        <p className="hint">Each connector receives call, lead, ticket and booking events. Failed pushes retry automatically with back-off (1 min → 2 h, 5 attempts); use “Send sample” to check your field mapping end-to-end.</p>
        <table>
          <thead><tr><th>Name</th><th>App</th><th>Account / target</th><th>Events</th><th>Last sync</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {connectors.map((c) => (
              <tr key={c.id}>
                <td>{c.name}{!c.enabled && <span className="pill" style={{ marginLeft: 6 }}>paused</span>}</td>
                <td><span className="pill">{info(c.provider)?.label ?? c.provider}</span></td>
                <td className="small muted">{c.account_label ?? c.target_url ?? "—"}</td>
                <td className="small">{c.triggers.map((t) => TRIGGERS.find(([id]) => id === t)?.[1] ?? t).join(", ")}{c.qualified_only ? " (qualified only)" : ""}</td>
                <td className="small">{c.last_sync_at ? when(c.last_sync_at) : "never"}</td>
                <td>{pill(c.status)}{c.last_error && <span className="muted small"> {c.last_error}</span>}</td>
                <td className="small" style={{ whiteSpace: "nowrap" }}>
                  {p.canManage && (
                    <>
                      <button onClick={() => test(c)}>Test</button> <button onClick={() => sample(c)}>Send sample</button>{" "}
                      <button onClick={() => setEditing(c)}>Edit</button> <button onClick={() => toggle(c)}>{c.enabled ? "Pause" : "Resume"}</button>{" "}
                      {info(c.provider)?.auth === "url" && <><button onClick={() => showSecret(c)}>Secret</button>{" "}</>}
                      <button onClick={() => remove(c)}>Remove</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
            {!connectors.length && <tr><td colSpan={7} className="muted">Nothing connected yet — add a CRM, Zapier/Make hook, Teams channel or Google Sheet below.</td></tr>}
          </tbody>
        </table>
        {msg && <p className="muted small" style={{ marginTop: 8 }}>{msg}</p>}
      </div>

      {p.canManage && (
        <ConnectorForm
          key={editing?.id ?? `new-${formKey}`}
          tenant={p.tenant}
          providers={p.providers}
          payloadFields={p.payloadFields}
          existing={editing}
          onDone={(c) => { if (c) upsert(c); setEditing(null); setFormKey((k) => k + 1); }}
          onMsg={setMsg}
        />
      )}

      <div className="section">
        <h2>Sync log</h2>
        <table>
          <thead><tr><th>When</th><th>App</th><th>Event</th><th>Attempts</th><th>Reference</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {jobs.slice(0, 100).map((j) => (
              <tr key={j.id}>
                <td className="small">{when(j.created_at)}</td>
                <td className="small">{connectors.find((c) => c.id === j.connector_id)?.name ?? info(j.provider)?.label ?? j.provider}</td>
                <td className="small">{j.event}</td>
                <td className="small">{j.attempts}{j.status === "retry" && j.next_attempt_at ? ` · next ${when(j.next_attempt_at)}` : ""}</td>
                <td className="small muted">{j.external_ref ?? "—"}</td>
                <td>{pill(j.status)}{j.error && <span className="muted small"> {j.error}</span>}</td>
                <td className="small">{p.canManage && (j.status === "failed" || j.status === "retry") && <button onClick={() => retry(j)}>Retry now</button>}</td>
              </tr>
            ))}
            {!jobs.length && <tr><td colSpan={7} className="muted">No sync activity yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <ApiKeys tenant={p.tenant} canManage={p.canManage} initial={p.apiKeys} />
      <Exports tenant={p.tenant} />
    </>
  );
}

function ConnectorForm({ tenant, providers, payloadFields, existing, onDone, onMsg }: {
  tenant: string; providers: ProviderInfo[]; payloadFields: string[]; existing: Connector | null;
  onDone: (c: Connector | null) => void; onMsg: (m: string) => void;
}) {
  const q = `?tenant_id=${tenant}`;
  const [provider, setProvider] = useState<ConnectorProvider>(existing?.provider ?? "hubspot");
  const info = providers.find((x) => x.provider === provider);
  const [name, setName] = useState(existing?.name ?? "");
  const [url, setUrl] = useState(existing?.target_url ?? "");
  const [secret, setSecret] = useState("");
  const [options, setOptions] = useState<Record<string, string>>(existing?.options ?? {});
  const [triggers, setTriggers] = useState<ConnectorTrigger[]>(existing?.triggers ?? ["lead.qualified", "ticket.created"]);
  const [qualifiedOnly, setQualifiedOnly] = useState(existing?.qualified_only ?? false);
  const [map, setMap] = useState<[string, string][]>(existing ? Object.entries(existing.field_map) : []);
  const [busy, setBusy] = useState(false);

  const optionKeys = info?.fields ?? [];
  const extraKeys = provider === "salesforce" ? [] : provider === "zoho" ? ["client_id", "client_secret", "accounts_domain"] : [];

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const field_map = Object.fromEntries(map.filter(([k, v]) => k && v));
    if (info?.auth === "oauth" && !existing) {
      const params = new URLSearchParams({ tenant_id: tenant, provider, ...options });
      const r = await request<{ url: string }>(`/v1/connectors/oauth/start?${params}`);
      setBusy(false);
      if (!r.ok) return onMsg(`OAuth not available: ${r.error}`);
      window.location.href = r.data.url;
      return;
    }
    const body: Record<string, unknown> = { name, triggers, qualified_only: qualifiedOnly, target_url: url || null, options, field_map };
    if (secret) body.secret = secret;
    const r = existing
      ? await request<Connector>(`/v1/connectors/${existing.id}${q}`, { method: "PATCH", body: JSON.stringify(body) })
      : await request<Connector>(`/v1/connectors${q}`, { method: "POST", body: JSON.stringify({ provider, ...body }) });
    setBusy(false);
    if (!r.ok) return onMsg(`Could not save: ${r.error}`);
    onMsg(existing ? "Connector updated." : `Connected: ${r.data.status}${r.data.last_error ? ` — ${r.data.last_error}` : ""}`);
    onDone(r.data);
    setSecret("");
  };

  return (
    <form className="section" onSubmit={submit}>
      <h2>{existing ? `Edit ${existing.name}` : "Add a connector"}</h2>
      <div className="grid">
        <label>App
          <select value={provider} disabled={!!existing} onChange={(e) => { setProvider(e.target.value as ConnectorProvider); setOptions({}); }}>
            {CATEGORIES.map(([cat, label]) => {
              const items = providers.filter((x) => x.category === cat);
              return items.length ? (
                <optgroup key={cat} label={label}>
                  {items.map((x) => <option key={x.provider} value={x.provider} disabled={!x.available}>{x.label}{x.available ? "" : " (not configured on this server)"}</option>)}
                </optgroup>
              ) : null;
            })}
          </select>
        </label>
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder={info?.label} /></label>
        {info?.auth === "url" && <label>Webhook URL<input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.zapier.com/hooks/catch/…" required /></label>}
        {info?.auth === "api_key" && (
          <label>{provider === "salesforce" ? "Consumer secret" : provider === "zoho" ? "Refresh token" : "API key / access token"}
            <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={existing?.has_secret ? "(unchanged)" : ""} required={!existing} autoComplete="off" />
          </label>
        )}
        {[...optionKeys, ...extraKeys].map((k) => (
          <label key={k}>{OPTION_LABELS[k]?.[0] ?? k}
            <input value={options[k] ?? ""} onChange={(e) => setOptions((o) => ({ ...o, [k]: e.target.value }))} placeholder={OPTION_LABELS[k]?.[1]} required={optionKeys.includes(k) && k !== "sheet"} />
          </label>
        ))}
      </div>
      {info?.help && <p className="hint">{info.help}</p>}
      <div className="chips" style={{ marginBottom: "0.8rem" }}>
        {TRIGGERS.map(([id, label]) => (
          <a key={id} className={triggers.includes(id) ? "active" : ""} onClick={() => setTriggers((ts) => (ts.includes(id) ? ts.filter((x) => x !== id) : [...ts, id]))}>{label}</a>
        ))}
      </div>
      <label className="check"><input type="checkbox" checked={qualifiedOnly} onChange={(e) => setQualifiedOnly(e.target.checked)} /> Skip spam, wrong numbers and existing customers on “every completed call”</label>

      <h3 style={{ marginTop: "1rem" }}>Field mapping <span className="muted small">(optional — rename ParlioTec fields to your app’s column/property names)</span></h3>
      {map.map(([k, v], i) => (
        <div key={i} className="grid" style={{ alignItems: "end" }}>
          <label>Their field<input value={k} onChange={(e) => setMap((m) => m.map((x, j) => (j === i ? [e.target.value, x[1]] : x)))} placeholder="e.g. Phone Number" /></label>
          <label>ParlioTec field
            <select value={v} onChange={(e) => setMap((m) => m.map((x, j) => (j === i ? [x[0], e.target.value] : x)))}>
              <option value="">—</option>
              {payloadFields.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <div><button type="button" onClick={() => setMap((m) => m.filter((_, j) => j !== i))}>Remove</button></div>
        </div>
      ))}
      <div style={{ marginTop: "0.6rem" }}>
        <button type="button" onClick={() => setMap((m) => [...m, ["", ""]])}>Add mapping</button>{" "}
        <button type="submit" className="primary" disabled={busy || !triggers.length}>{info?.auth === "oauth" && !existing ? "Sign in and connect" : existing ? "Save changes" : "Connect"}</button>{" "}
        {existing && <button type="button" onClick={() => onDone(null)}>Cancel</button>}
      </div>
    </form>
  );
}

function ApiKeys({ tenant, canManage, initial }: { tenant: string; canManage: boolean; initial: TenantApiKey[] }) {
  const [keys, setKeys] = useState(initial);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<TenantApiKey | null>(null);
  const q = `?tenant_id=${tenant}`;
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await request<TenantApiKey>(`/v1/api-keys${q}`, { method: "POST", body: JSON.stringify({ name }) });
    if (!r.ok) return;
    setKeys((ks) => [...ks, r.data]); setFresh(r.data); setName("");
  };
  const revoke = async (k: TenantApiKey) => {
    if (!confirm(`Revoke "${k.name}"? Anything using it stops working immediately.`)) return;
    if (await del(`/v1/api-keys/${k.id}${q}`)) setKeys((ks) => ks.filter((x) => x.id !== k.id));
  };
  return (
    <div className="section">
      <h2>Inbound API keys</h2>
      <p className="hint">Let your own systems (or a Zap) push data into ParlioTec: <code>POST {API_URL}/v1/inbound/contacts</code> to pre-load callers (VIP flags, names), <code>POST /v1/inbound/tickets</code> to raise a callback, <code>GET /v1/inbound/calls</code> to pull call history. Send the key as <code>Authorization: Bearer …</code> or <code>X-Api-Key</code>.</p>
      {fresh?.key && (
        <p className="small" style={{ padding: "0.6rem 0.8rem", border: "1px solid var(--border)", borderRadius: 8 }}>
          Copy this key now — it is shown once:<br /><code style={{ userSelect: "all" }}>{fresh.key}</code>
        </p>
      )}
      <table>
        <thead><tr><th>Name</th><th>Key</th><th>Created</th><th>Last used</th><th></th></tr></thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k.id}>
              <td>{k.name}</td><td className="small muted">{k.prefix}…</td><td className="small">{when(k.created_at)}</td>
              <td className="small">{k.last_used_at ? when(k.last_used_at) : "never"}</td>
              <td>{canManage && <button onClick={() => revoke(k)}>Revoke</button>}</td>
            </tr>
          ))}
          {!keys.length && <tr><td colSpan={5} className="muted">No keys issued.</td></tr>}
        </tbody>
      </table>
      {canManage && (
        <form onSubmit={create} className="form" style={{ marginTop: "0.8rem" }}>
          <div className="grid" style={{ alignItems: "end", marginBottom: 0 }}>
            <label>Key name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Website form" required maxLength={60} /></label>
            <div><button type="submit" className="primary">Create key</button></div>
          </div>
        </form>
      )}
    </div>
  );
}

function Exports({ tenant }: { tenant: string }) {
  const [msg, setMsg] = useState<string | null>(null);
  const dl = async (what: "calls" | "contacts" | "tickets") => {
    const err = await downloadCsv(what, tenant);
    setMsg(err ? `Export failed: ${err}` : `Downloading ${what}.csv`);
  };
  return (
    <div className="section">
      <h2>CSV export</h2>
      <p className="hint">Download your data for Excel, Google Sheets or any tool without an integration.</p>
      <button onClick={() => dl("calls")}>Calls</button> <button onClick={() => dl("contacts")}>Contacts</button> <button onClick={() => dl("tickets")}>Tickets</button>
      {msg && <span className="muted small" style={{ marginLeft: 8 }}>{msg}</span>}
    </div>
  );
}
