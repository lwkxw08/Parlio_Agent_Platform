"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type FaultReport,
  type HealthView,
  type TrunkHealth,
  diagnoseForwarding,
  diagnoseTrunk,
  fetchHealthView,
  runSynthetic,
  when,
} from "@/lib/api";
import { humanize } from "@/app/breakdown";

export const gradeCls = (g: string) => (g === "healthy" ? "ok" : g === "watch" ? "warn" : g === "inactive" ? "" : "bad");
export const fmt = (v: number | null, unit: string) => (v == null ? "—" : `${Number.isInteger(v) ? v : v.toFixed(unit === "%" ? 0 : 2)}${unit === "%" ? "%" : unit ? ` ${unit}` : ""}`);
const ATTR: Record<FaultReport["attribution"], string> = {
  parlio: "Parlio platform", carrier: "Carrier", customer_provider: "Your phone provider / PBX", customer_config: "Your configuration", unknown: "Undetermined",
};

export function TrunkTable({ trunks, onDiagnose }: { trunks: TrunkHealth[]; onDiagnose?: (id: string) => void }) {
  if (!trunks.length) return <p className="muted small">No SIP trunks configured — calls arrive on your Parlio number.</p>;
  return (
    <table>
      <thead><tr><th>Trunk</th><th>Registration</th><th>OPTIONS</th><th>INVITE failures</th><th>MOS</th><th>Jitter</th><th>Loss</th><th>Issues</th>{onDiagnose && <th />}</tr></thead>
      <tbody>
        {trunks.map((t) => (
          <tr key={t.trunk_id}>
            <td>{t.name} <span className="pill">{humanize(t.mode)}</span></td>
            <td><span className={`pill ${t.healthy ? "ok" : "bad"}`}>{humanize(t.registration)}</span>{t.registration_detail && <div className="small muted">{t.registration_detail}</div>}</td>
            <td>{t.options_ping_ok == null ? "—" : t.options_ping_ok ? `ok ${t.options_rtt_ms ?? ""}ms` : "failed"}</td>
            <td>{t.invite_failures}/{t.invites}{t.auth_failures ? ` (${t.auth_failures} auth)` : ""}</td>
            <td>{fmt(t.audio.mos_avg, "")}</td><td>{fmt(t.audio.jitter_ms_avg, "ms")}</td><td>{fmt(t.audio.packet_loss_pct_avg, "%")}</td>
            <td className="small">{t.issues.length ? t.issues.join("; ") : <span className="muted">none</span>}</td>
            {onDiagnose && <td><button className="ghost" onClick={() => onDiagnose(t.trunk_id)}>Diagnose</button></td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function FaultCard({ f }: { f: FaultReport }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card" style={{ marginBottom: "0.8rem" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{f.headline}</strong>
        <span className={`pill ${f.attribution === "parlio" ? "bad" : f.attribution === "unknown" ? "" : "warn"}`}>{ATTR[f.attribution]} · {Math.round(f.confidence * 100)}%</span>
      </div>
      <p className="small">{f.explanation}</p>
      {f.next_steps.length > 0 && <ol className="small">{f.next_steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
      <div className="row small muted">
        <span>{f.subject} · {when(f.created_at)}</span>
        <button className="ghost" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} provider report</button>
        {open && <button className="ghost" onClick={() => navigator.clipboard.writeText(f.provider_report)}>Copy</button>}
      </div>
      {open && <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{f.provider_report}</pre>}
    </div>
  );
}

export default function Health({ tenant, view: initial }: { tenant: string; view: HealthView }) {
  const [v, setV] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const reload = async () => { const r = await fetchHealthView(tenant); if (r) setV(r); };
  const act = async (fn: () => Promise<{ ok: boolean; error?: string }>, done: string) => {
    setBusy(true);
    const r = await fn();
    setMsg(r.ok ? done : (r.error ?? "Failed"));
    await reload();
    setBusy(false);
  };
  const h = v.health;
  const fwd = v.forwarding;
  return (
    <>
      <div className="grid">
        <div className="card">
          <div className="label">Health score ({h.days}d)</div>
          <div className="value">{h.score} <span className={`pill ${gradeCls(h.grade)}`}>{h.grade.replace("_", " ")}</span></div>
          <div className="small muted">{h.calls_period} calls · {h.open_alerts} open alerts · updated {when(h.computed_at)}</div>
        </div>
        <div className="card">
          <div className="label">Call forwarding</div>
          <div className="value"><span className={`pill ${fwd.status === "ok" ? "ok" : fwd.status === "forwarding_may_be_off" ? "bad" : ""}`}>{fwd.status.replaceAll("_", " ")}</span></div>
          <div className="small muted">{fwd.detail || "Baseline learns from your normal call pattern."} {fwd.status === "forwarding_may_be_off" && <Link href={`/telephony?tenant=${tenant}`}>Forwarding guide</Link>}</div>
        </div>
        <div className="card">
          <div className="label">Synthetic test</div>
          <div className="value">{v.synthetic[0] ? <span className={`pill ${v.synthetic[0].passed ? "ok" : "bad"}`}>{v.synthetic[0].passed ? "passing" : "failing"}</span> : "—"}</div>
          <div className="small muted">{v.synthetic[0] ? `last ${when(v.synthetic[0].created_at)} (${v.synthetic[0].trigger})` : "runs daily, after deploys and config changes"}</div>
          <div className="row" style={{ marginTop: "0.5rem" }}>
            <button className="primary" disabled={busy} onClick={() => act(() => runSynthetic(tenant), "Synthetic call completed")}>Run test call now</button>
          </div>
        </div>
      </div>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}

      <div className="section">
        <h2>Signals</h2>
        <table>
          <thead><tr><th>Signal</th><th>Value</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>
            {h.signals.map((s) => (
              <tr key={s.key}><td>{s.label}</td><td>{fmt(s.value, s.unit)}</td><td><span className={`pill ${s.ok ? "ok" : "bad"}`}>{s.ok ? "ok" : "attention"}</span></td><td className="small muted">{s.detail}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="section">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>SIP trunks</h2>
          <button className="ghost" disabled={busy} onClick={() => act(() => diagnoseForwarding(tenant), "Forwarding diagnosis added below")}>Diagnose forwarding</button>
        </div>
        <TrunkTable trunks={v.trunks} onDiagnose={(id) => act(() => diagnoseTrunk(tenant, id), "Trunk diagnosis added below")} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="section">
          <h2>Open alerts</h2>
          {v.alerts.length === 0 ? <p className="muted small">Nothing needs attention.</p> : v.alerts.map((a) => (
            <div key={a.id} className="row small" style={{ marginBottom: "0.4rem" }}>
              <span className={`pill ${a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : ""}`}>{humanize(a.severity)}</span>
              <span><strong>{a.title}</strong> — {a.detail}</span>
              <span className="muted">{when(a.opened_at)}</span>
            </div>
          ))}
        </div>
        <div className="section">
          <h2>Synthetic call history</h2>
          {v.synthetic.length === 0 ? <p className="muted small">No runs yet.</p> : (
            <table>
              <thead><tr><th>When</th><th>Trigger</th><th>Result</th><th>Checks</th></tr></thead>
              <tbody>
                {v.synthetic.slice(0, 15).map((r) => (
                  <tr key={r.id}>
                    <td>{when(r.created_at)}</td><td>{r.trigger} · {r.mode}</td>
                    <td><span className={`pill ${r.passed ? "ok" : "bad"}`}>{r.passed ? "pass" : "fail"}</span>{r.ticket_id && <Link href={`/support?tenant=${tenant}`} className="small"> ticket</Link>}</td>
                    <td className="small">{r.checks.map((c) => <span key={c.path} className={`pill ${c.passed ? "ok" : "bad"}`} title={c.detail} style={{ marginRight: 4 }}>{c.path}</span>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="section">
        <h2>Fault reports</h2>
        <p className="hint">Each diagnosis says whether the cause sits with Parlio, the carrier, your phone provider/PBX or your configuration, with a ready-to-send provider report. Diagnose a specific call from its detail page.</p>
        {v.faults.length === 0 ? <p className="muted small">No fault reports.</p> : v.faults.map((f) => <FaultCard key={f.id} f={f} />)}
      </div>
    </>
  );
}
