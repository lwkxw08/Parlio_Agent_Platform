"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type HealthView,
  type Incident,
  type OnCallConfig,
  type OpsOverview,
  fetchIncidents,
  fetchOpsOverview,
  fetchOpsTenant,
  put,
  request,
  when,
} from "@/lib/api";
import { Stat } from "../ui";
import { FaultCard, TrunkTable, fmt, gradeCls } from "../../health/health";
import { STATE_LABEL, stateCls } from "../../status/state";

const TABS = [["board", "Tenant health"], ["alerts", "Alerts"], ["status", "Status & incidents"], ["oncall", "On-call & failover"]] as const;
type Tab = (typeof TABS)[number][0];
const sevCls = (s: string) => (s === "critical" ? "bad" : s === "warning" ? "warn" : "");
const newIncident = (): Incident => ({
  id: `inc-${Math.random().toString(16).slice(2, 10)}`, title: "", severity: "p2", components: ["voice_uk"], impact: "degraded", status: "investigating",
  updates: [], started_at: new Date().toISOString(), resolved_at: null, rca_due_at: null, rca: null, created_by: null,
});

export default function Ops({ overview: initial, incidents: initialInc, canAct }: { overview: OpsOverview; incidents: Incident[]; canAct: boolean }) {
  const [o, setO] = useState(initial);
  const [incidents, setIncidents] = useState(initialInc);
  const [tab, setTab] = useState<Tab>("board");
  const [detail, setDetail] = useState<HealthView | null>(null);
  const [inc, setInc] = useState<Incident>(newIncident());
  const [update, setUpdate] = useState({ status: "investigating" as Incident["status"], message: "" });
  const [oncall, setOncall] = useState<OnCallConfig>(initial.oncall);
  const [failReason, setFailReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = async () => {
    const [ov, ins] = await Promise.all([fetchOpsOverview(), fetchIncidents()]);
    if (ov) { setO(ov); setOncall(ov.oncall); }
    if (ins) setIncidents(ins);
  };
  const act = async (path: string, body?: unknown, method = "POST") => {
    setBusy(true);
    const r = await request<unknown>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
    setMsg(r.ok ? "Done" : r.error);
    await reload();
    setBusy(false);
    return r.ok;
  };
  const openTenant = async (id: string) => { const v = await fetchOpsTenant(id); setDetail(v); };
  const saveIncident = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<Incident>(`/v1/admin/ops/incidents/${inc.id}`, inc);
    if (!r.ok) return setMsg(r.error);
    setInc(newIncident()); setMsg("Incident published to the status page"); await reload();
  };
  const postUpdate = async (id: string) => {
    if (!update.message.trim()) return;
    if (await act(`/v1/admin/ops/incidents/${id}/updates`, { status: update.status, message: update.message })) setUpdate({ ...update, message: "" });
  };
  const saveOncall = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<OnCallConfig>("/v1/admin/ops/oncall", oncall);
    setMsg(r.ok ? "On-call configuration saved" : r.error);
    await reload();
  };

  const st = o.status;
  return (
    <>
      <div className="grid">
        <Stat label="Platform" value={<span className={`pill ${stateCls(st.overall)}`}>{STATE_LABEL[st.overall]}</span>} sub={`${st.uptime_30d_pct.toFixed(2)}% voice uptime (30d) · `} />
        <Stat label="Open alerts" value={o.open_alerts.length} sub={Object.entries(o.alerts).map(([k, v]) => `${v} ${k}`).join(" · ") || "none"} />
        <Stat label="Tenants at risk" value={o.board.filter((t) => t.grade === "at_risk" || t.grade === "critical").length} sub={`${o.board.length} monitored`} />
        <Stat label="Support queue" value={o.open_tickets} sub={<>{o.breached_tickets} SLA breached · <Link href="/admin/support">desk</Link></>} />
        <Stat label="Canary gate" value={<span className={`pill ${o.canary_ok ? "ok" : "bad"}`}>{o.canary_ok ? "rollout OK" : "hold"}</span>} sub={o.canary_reasons.join("; ") || "answer rate, latency and QA within thresholds"} />
        <Stat label="Carrier" value={o.failover.active} sub={`${o.failover.auto ? "auto" : "manual"} failover · secondary ${o.failover.secondary_carrier}`} />
      </div>
      {canAct && (
        <div className="row" style={{ marginBottom: "1rem" }}>
          <button className="primary" disabled={busy} onClick={() => act("/v1/admin/ops/sweep?synthetic=true")}>Run health sweep + synthetic calls</button>
          <button className="ghost" disabled={busy} onClick={() => act("/v1/admin/ops/synthetic?trigger=post_deploy")}>Post-deploy synthetic run</button>
          {msg && <span className="small" style={{ color: "var(--accent)" }}>{msg}</span>}
        </div>
      )}
      <div className="tabs">{TABS.map(([k, l]) => <a key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)} style={{ cursor: "pointer" }}>{l}</a>)}</div>

      {tab === "board" && (
        <div className="grid" style={{ gridTemplateColumns: detail ? "1fr 1.4fr" : "1fr" }}>
          <div className="section">
            <h2>Tenant health board</h2>
            <table>
              <thead><tr><th>Tenant</th><th>Score</th><th>Calls (7d)</th><th>Alerts</th><th>Attention</th></tr></thead>
              <tbody>
                {o.board.map((t) => (
                  <tr key={t.tenant_id} onClick={() => openTenant(t.tenant_id)} style={{ cursor: "pointer", background: detail?.health.tenant_id === t.tenant_id ? "var(--hover)" : undefined }}>
                    <td><strong>{t.name || t.tenant_id}</strong><div className="small muted">{t.tenant_id}</div></td>
                    <td>{t.score} <span className={`pill ${gradeCls(t.grade)}`}>{t.grade.replace("_", " ")}</span></td>
                    <td>{t.calls_period}</td><td>{t.open_alerts}</td>
                    <td className="small">{t.signals.filter((s) => !s.ok).map((s) => s.label).join(", ") || <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {detail && (
            <div className="section">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h2>{detail.health.name || detail.health.tenant_id}</h2>
                <span><Link href={`/admin/tenants/${detail.health.tenant_id}`} className="small">Tenant detail</Link> · <Link href={`/admin/support?tenant=${detail.health.tenant_id}`} className="small">Tickets</Link></span>
              </div>
              {canAct && <div className="row" style={{ marginBottom: "0.6rem" }}>
                <button className="ghost" disabled={busy} onClick={async () => { await act(`/v1/admin/ops/synthetic/${detail.health.tenant_id}`); openTenant(detail.health.tenant_id); }}>Synthetic call</button>
                <button className="ghost" disabled={busy} onClick={async () => { await act(`/v1/admin/ops/support/tenants/${detail.health.tenant_id}/sip-diagnostics`); openTenant(detail.health.tenant_id); }}>SIP diagnostics</button>
              </div>}
              <table>
                <tbody>{detail.health.signals.map((s) => <tr key={s.key}><td>{s.label}</td><td>{fmt(s.value, s.unit)}</td><td><span className={`pill ${s.ok ? "ok" : "bad"}`}>{s.ok ? "ok" : "attention"}</span></td><td className="small muted">{s.detail}</td></tr>)}</tbody>
              </table>
              <p className="small" style={{ marginTop: "0.6rem" }}><strong>Forwarding:</strong> <span className={`pill ${detail.forwarding.status === "ok" ? "ok" : "warn"}`}>{detail.forwarding.status.replaceAll("_", " ")}</span> {detail.forwarding.detail}</p>
              <TrunkTable trunks={detail.trunks} onDiagnose={canAct ? (id) => act(`/v1/admin/ops/trunks/${detail.health.tenant_id}/${id}/remediate`).then(() => openTenant(detail.health.tenant_id)) : undefined} />
              {detail.synthetic.length > 0 && <p className="small muted" style={{ marginTop: "0.6rem" }}>Last synthetic: {detail.synthetic[0].passed ? "pass" : "fail"} · {when(detail.synthetic[0].created_at)} ({detail.synthetic[0].trigger})</p>}
              {detail.faults.slice(0, 3).map((f) => <FaultCard key={f.id} f={f} />)}
            </div>
          )}
        </div>
      )}

      {tab === "alerts" && (
        <div className="section">
          <h2>Open alerts</h2>
          {o.open_alerts.length === 0 ? <p className="muted small">All clear.</p> : (
            <table>
              <thead><tr><th>Severity</th><th>Tenant</th><th>Alert</th><th>Opened</th><th>Acknowledged by</th>{canAct && <th />}</tr></thead>
              <tbody>
                {o.open_alerts.map((a) => (
                  <tr key={a.id}>
                    <td><span className={`pill ${sevCls(a.severity)}`}>{a.severity}</span>{a.paged && <span className="pill accent" style={{ marginLeft: 4 }}>paged</span>}</td>
                    <td><a onClick={() => { setTab("board"); openTenant(a.tenant_id); }} style={{ cursor: "pointer" }}>{a.tenant_id}</a></td>
                    <td><strong>{a.title}</strong><div className="small muted">{a.detail}</div></td>
                    <td className="small">{when(a.opened_at)}</td>
                    <td className="small">{a.acknowledged_by ?? "—"}</td>
                    {canAct && <td className="row">
                      {!a.acknowledged_by && <button className="ghost" disabled={busy} onClick={() => act(`/v1/admin/ops/alerts/${a.id}/ack`)}>Acknowledge</button>}
                      <button className="ghost" disabled={busy} onClick={() => act(`/v1/admin/ops/alerts/${a.id}/resolve`)}>Resolve</button>
                    </td>}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === "status" && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div>
            <div className="section">
              <h2>Components (auto-updated from monitors)</h2>
              <p className="hint">Public at <Link href="/status">/status</Link>. Manual incidents override monitor state for the components they list.</p>
              <table><tbody>{st.components.map((c) => <tr key={c.id}><td>{c.name}</td><td><span className={`pill ${stateCls(c.state)}`}>{STATE_LABEL[c.state]}</span></td><td className="small muted">{c.detail} · {c.source}</td></tr>)}</tbody></table>
            </div>
            {canAct && (
              <form className="section form" onSubmit={saveIncident}>
                <h2>Declare incident</h2>
                <label>Title<input required value={inc.title} onChange={(e) => setInc({ ...inc, title: e.target.value })} maxLength={160} /></label>
                <div className="two">
                  <label>Severity<select value={inc.severity} onChange={(e) => setInc({ ...inc, severity: e.target.value as Incident["severity"] })}><option value="p1">P1</option><option value="p2">P2</option><option value="p3">P3</option></select></label>
                  <label>Impact<select value={inc.impact} onChange={(e) => setInc({ ...inc, impact: e.target.value as Incident["impact"] })}>{(Object.keys(STATE_LABEL) as Incident["impact"][]).filter((k) => k !== "operational").map((k) => <option key={k} value={k}>{STATE_LABEL[k]}</option>)}</select></label>
                </div>
                <label>Components
                  <select multiple value={inc.components} onChange={(e) => setInc({ ...inc, components: Array.from(e.target.selectedOptions).map((x) => x.value) })}>
                    {st.components.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </label>
                <button className="primary" type="submit">Publish</button>
              </form>
            )}
          </div>
          <div className="section">
            <h2>Incidents</h2>
            {incidents.length === 0 && <p className="muted small">None recorded.</p>}
            {incidents.map((i) => (
              <div key={i.id} className="card" style={{ marginBottom: "0.8rem" }}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <strong>{i.title}</strong>
                  <span><span className={`pill ${i.status === "resolved" ? "ok" : "warn"}`}>{i.status}</span> <span className="pill">{i.severity.toUpperCase()}</span></span>
                </div>
                <div className="small muted">{when(i.started_at)} · {i.components.join(", ")}{i.rca_due_at && !i.rca && ` · RCA due ${when(i.rca_due_at)}`}</div>
                {i.updates.map((u, n) => <p key={n} className="small" style={{ margin: "0.2rem 0" }}><span className="muted">{when(u.at)}</span> <strong>{u.status}</strong> — {u.message}</p>)}
                {canAct && i.status !== "resolved" && (
                  <div className="form row" style={{ marginTop: "0.4rem" }}>
                    <select value={update.status} onChange={(e) => setUpdate({ ...update, status: e.target.value as Incident["status"] })}>{["investigating", "identified", "monitoring", "resolved"].map((s) => <option key={s}>{s}</option>)}</select>
                    <input value={update.message} onChange={(e) => setUpdate({ ...update, message: e.target.value })} placeholder="Update message" style={{ flex: 1 }} />
                    <button className="ghost" disabled={busy} onClick={() => postUpdate(i.id)}>Post</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "oncall" && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <form className="section form" onSubmit={saveOncall}>
            <h2>On-call paging</h2>
            <p className="hint">Critical alerts and P1 support tickets page the primary on-call via PagerDuty Events v2, Opsgenie or a generic webhook.</p>
            <fieldset disabled={!canAct} style={{ border: 0, padding: 0, margin: 0, display: "contents" }}>
              <label>Provider<select value={oncall.provider} onChange={(e) => setOncall({ ...oncall, provider: e.target.value as OnCallConfig["provider"] })}><option value="none">None (log only)</option><option value="pagerduty">PagerDuty</option><option value="opsgenie">Opsgenie</option><option value="webhook">Webhook</option></select></label>
              {oncall.provider !== "webhook" && oncall.provider !== "none" && <label>Integration / API key<input type="password" value={oncall.routing_key ?? ""} onChange={(e) => setOncall({ ...oncall, routing_key: e.target.value || null })} /></label>}
              {oncall.provider === "webhook" && <label>Webhook URL<input type="url" value={oncall.webhook_url ?? ""} onChange={(e) => setOncall({ ...oncall, webhook_url: e.target.value || null })} /></label>}
              <label>Rota (staff emails, primary first)<textarea rows={3} value={oncall.rota.join("\n")} onChange={(e) => setOncall({ ...oncall, rota: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} /></label>
              <label className="check"><input type="checkbox" checked={oncall.page_on.includes("warning")} onChange={(e) => setOncall({ ...oncall, page_on: e.target.checked ? ["critical", "warning"] : ["critical"] })} /> Also page on warnings</label>
              {canAct && <button className="primary" type="submit">Save</button>}
            </fieldset>
            {oncall.updated_by && <p className="small muted">Last changed by {oncall.updated_by} · {when(oncall.updated_at)}</p>}
          </form>
          <div className="section form">
            <h2>Carrier failover</h2>
            <p className="hint">Active carrier for new numbers and outbound routing. Automatic failover triggers when the SIP edge or carrier component goes to major outage; switching back is manual.</p>
            <p>Primary <strong>{o.failover.primary_carrier}</strong> · secondary <strong>{o.failover.secondary_carrier}</strong> · active <span className="pill accent">{o.failover.active}</span> · region {o.failover.region}</p>
            {o.failover.last_switch_at && <p className="small muted">Last switch {when(o.failover.last_switch_at)}: {o.failover.reason}</p>}
            {canAct && (
              <div className="row">
                <input value={failReason} onChange={(e) => setFailReason(e.target.value)} placeholder="Reason (audited)" style={{ flex: 1 }} />
                <button className="danger" disabled={busy || !failReason.trim()} onClick={() => act("/v1/admin/ops/failover", { to: o.failover.active === o.failover.primary_carrier ? o.failover.secondary_carrier : o.failover.primary_carrier, reason: failReason }).then(() => setFailReason(""))}>
                  Switch to {o.failover.active === o.failover.primary_carrier ? o.failover.secondary_carrier : o.failover.primary_carrier}
                </button>
              </div>
            )}
            <h2 style={{ marginTop: "1rem" }}>Runbooks</h2>
            <ul className="small">
              <li><a href="https://github.com/lwkxw08/Parlio_Agent_Platform/blob/main/docs/runbooks/README.md" target="_blank" rel="noreferrer">Ops runbooks &amp; RCA template</a></li>
              <li>P1: ack within 15 min, status page update within 30 min, RCA within 48 h for Enterprise.</li>
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
