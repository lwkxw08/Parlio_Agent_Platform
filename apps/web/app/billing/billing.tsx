"use client";

import { useState } from "react";
import {
  type Assistant,
  type Entitlements,
  type AvailableNumber,
  type CheckoutSession,
  type Coupon,
  type LatencyBucket,
  type LatencyReport,
  type Plan,
  type Subscription,
  type TenantNumber,
  type UsageSummary,
  del,
  gbp,
  ms,
  request,
  when,
} from "@/lib/api";

const TABS = [["usage", "Usage"], ["plan", "Plan"], ["numbers", "Numbers"], ["latency", "Latency"]] as const;
type Tab = (typeof TABS)[number][0];

const statusPill = (s: Subscription["status"]) => {
  const cls = s === "active" ? "ok" : s === "past_due" || s === "cancelled" ? "bad" : "warn";
  return <span className={`pill ${cls}`}>{s.replace("_", " ")}</span>;
};

const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });

export default function Billing(props: {
  tenant: string; canManage: boolean; initialTab: string; plans: Plan[]; subscription: Subscription; usage: UsageSummary;
  numbers: TenantNumber[]; latency: LatencyReport | null; assistants: Assistant[]; entitlements: Entitlements | null;
}) {
  const { tenant, canManage, plans, assistants, latency, entitlements } = props;
  const [tab, setTab] = useState<Tab>(TABS.some(([t]) => t === props.initialTab) ? (props.initialTab as Tab) : "usage");
  const [sub, setSub] = useState(props.subscription);
  const [usage, setUsage] = useState(props.usage);
  const [numbers, setNumbers] = useState(props.numbers);
  const [msg, setMsg] = useState<string | null>(null);
  const q = `?tenant_id=${tenant}`;

  const reload = async () => {
    const [s, u] = await Promise.all([request<Subscription>(`/v1/billing/subscription${q}`), request<UsageSummary>(`/v1/billing/usage${q}`)]);
    if (s.ok) setSub(s.data);
    if (u.ok) setUsage(u.data);
  };

  return (
    <>
      <div className="tabs">
        {TABS.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      {tab === "usage" && <Usage usage={usage} sub={sub} />}
      {tab === "plan" && <PlanTab tenant={tenant} canManage={canManage} plans={plans} sub={sub} entitlements={entitlements} onChange={reload} setMsg={setMsg} />}
      {tab === "numbers" && (
        <Numbers tenant={tenant} canManage={canManage} numbers={numbers} setNumbers={setNumbers} usage={usage} assistants={assistants} setMsg={setMsg} onChange={reload} />
      )}
      {tab === "latency" && <Latency report={latency} tenant={tenant} />}
    </>
  );
}

function Usage({ usage: u, sub }: { usage: UsageSummary; sub: Subscription }) {
  const days = Object.entries(u.per_day_minutes).sort(([a], [b]) => a.localeCompare(b));
  const max = Math.max(1, ...days.map(([, v]) => v));
  const pctUsed = u.minutes_included ? Math.min(100, Math.round((u.minutes_used / u.minutes_included) * 100)) : 0;
  return (
    <>
      <div className="grid">
        <div className="card"><div className="label">Plan</div><div className="value">{u.plan.name}</div><div className="small">{statusPill(sub.status)} · renews {day(u.period_end)}</div></div>
        <div className="card"><div className="label">Minutes used</div><div className="value">{u.minutes_used.toFixed(1)}</div><div className="small muted">of {u.minutes_included} included · {pctUsed}%</div></div>
        <div className="card"><div className="label">Overage</div><div className="value">{u.minutes_overage.toFixed(1)} min</div><div className="small muted">{gbp(u.overage_pence)} at {u.plan.overage_pence_per_minute}p/min</div></div>
        <div className="card"><div className="label">Estimated bill</div><div className="value">{gbp(u.estimated_total_pence)}</div><div className="small muted">{gbp(u.base_pence)} plan{u.discount_pence ? ` − ${gbp(u.discount_pence)} coupon` : ""}{u.overage_pence ? ` + ${gbp(u.overage_pence)} overage` : ""}</div></div>
      </div>
      <div className="grid">
        <div className="card"><div className="label">Calls this period</div><div className="value">{u.calls}</div></div>
        <div className="card"><div className="label">SMS</div><div className="value">{u.sms_used}</div><div className="small muted">of {u.sms_included} included{u.sms_overage_pence ? ` · ${gbp(u.sms_overage_pence)} overage` : ""}</div></div>
        <div className="card"><div className="label">Numbers</div><div className="value">{u.numbers_used}</div><div className="small muted">of {u.numbers_included} included</div></div>
        <div className="card"><div className="label">Vendor cost / margin</div><div className="value">{gbp(Math.round(u.vendor_cost_pence))}</div><div className="small muted">{u.gross_margin_pct == null ? "—" : `${u.gross_margin_pct.toFixed(0)}% gross margin`} (STT+LLM+TTS+carrier)</div></div>
      </div>
      <div className="section">
        <h2>Minutes per day</h2>
        <p className="hint">Billed in 6-second blocks from answer to hang-up ({day(u.period_start)} – {day(u.period_end)}).</p>
        {days.length === 0 ? <p className="muted small">No calls yet this period.</p> : (
          <div className="bars" style={{ marginBottom: "1.4rem" }}>
            {days.map(([d, v]) => <div key={d} className="bar" style={{ height: `${(v / max) * 100}%` }} title={`${d}: ${v.toFixed(1)} min`}><span>{d.slice(8)}</span></div>)}
          </div>
        )}
      </div>
      {u.top_calls.length > 0 && (
        <div className="section">
          <h2>Largest calls</h2>
          <table>
            <thead><tr><th>Call</th><th>Minutes</th><th>Billable</th><th>Vendor cost</th></tr></thead>
            <tbody>
              {u.top_calls.map((c) => (
                <tr key={c.call_id}><td><a href={`/calls/${c.call_id}`}>{c.call_id}</a></td><td>{c.minutes.toFixed(1)}</td><td>{gbp(c.billable_pence)}</td><td>{c.vendor_pence.toFixed(1)}p</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function PlanTab({ tenant, canManage, plans, sub, entitlements, onChange, setMsg }: {
  tenant: string; canManage: boolean; plans: Plan[]; sub: Subscription; entitlements: Entitlements | null; onChange: () => Promise<void>; setMsg: (m: string | null) => void;
}) {
  const label = (k: string) => entitlements?.catalogue[k] ?? k;
  const [code, setCode] = useState(sub.coupon ?? "");
  const [coupon, setCoupon] = useState<Coupon | null>(null);
  const q = `?tenant_id=${tenant}`;

  const check = async (planId: string) => {
    if (!code.trim()) return setCoupon(null);
    const r = await request<Coupon>(`/v1/billing/coupon${q}`, { method: "POST", body: JSON.stringify({ code: code.trim(), plan_id: planId }) });
    if (r.ok) { setCoupon(r.data); setMsg(`Coupon ${r.data.code}: ${r.data.percent_off ? `${r.data.percent_off}% off` : `${gbp(r.data.amount_off_pence ?? 0)} off`}${r.data.months ? ` for ${r.data.months} months` : ""}`); }
    else { setCoupon(null); setMsg(`Coupon not valid: ${r.error}`); }
  };
  const choose = async (p: Plan) => {
    if (p.enterprise) return;
    if (sub.status === "trialing" || sub.status === "cancelled") {
      const r = await request<CheckoutSession>(`/v1/billing/checkout${q}`, { method: "POST", body: JSON.stringify({ plan_id: p.id, return_url: window.location.href }) });
      if (!r.ok) return setMsg(`Checkout failed: ${r.error}`);
      if (r.data.provider !== "simulated") { window.location.href = r.data.url; return; }
      setMsg("Simulated checkout completed — subscription is now active.");
    } else {
      const r = await request<Subscription>(`/v1/billing/subscription${q}`, { method: "POST", body: JSON.stringify({ plan_id: p.id, coupon: code.trim() || null }) });
      if (!r.ok) return setMsg(`Could not change plan: ${r.error}`);
      setMsg(`Moved to ${p.name}.`);
    }
    await onChange();
  };

  return (
    <>
      <div className="section">
        <h2>Current subscription</h2>
        <dl className="kv">
          <dt>Plan</dt><dd>{plans.find((p) => p.id === sub.plan_id)?.name ?? sub.plan_id} {statusPill(sub.status)}</dd>
          <dt>Period</dt><dd>{day(sub.period_start)} – {day(sub.period_end)}</dd>
          <dt>Coupon</dt><dd>{sub.coupon ? `${sub.coupon}${sub.coupon_months_left != null ? ` (${sub.coupon_months_left} months left)` : ""}` : "—"}</dd>
          <dt>Payments</dt><dd>{sub.provider === "simulated" ? <span className="pill warn">simulated — Stripe not connected</span> : sub.provider}</dd>
        </dl>
      </div>
      {canManage && (
        <div className="section">
          <h2>Coupon</h2>
          <p className="hint">Applied at checkout or on plan change.</p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="e.g. LAUNCH50" style={{ maxWidth: 200 }} />
            <button onClick={() => check(sub.plan_id === "starter" ? "growth" : sub.plan_id)}>Check</button>
          </div>
        </div>
      )}
      {entitlements && (
        <div className="section">
          <h2>Your features</h2>
          <p className="hint">{sub.status === "trialing" ? "Everything is unlocked during your trial; your plan's set applies once it converts." : "Locked features need a plan upgrade."}</p>
          <div className="chips">
            {Object.entries(entitlements.catalogue).map(([k, d]) => (
              <span key={k} className={`pill ${entitlements.enabled[k] ? "ok" : ""}`} title={d}>{entitlements.enabled[k] ? "" : ""}{d}</span>
            ))}
          </div>
        </div>
      )}
      <div className="grid">
        {plans.map((p) => {
          const current = p.id === sub.plan_id;
          const off = coupon?.percent_off ? Math.round(p.monthly_pence * (1 - coupon.percent_off / 100)) : coupon?.amount_off_pence ? Math.max(0, p.monthly_pence - coupon.amount_off_pence) : null;
          return (
            <div key={p.id} className="card" style={current ? { borderColor: "var(--accent)" } : undefined}>
              <div className="label">{p.name}{current && <span className="pill ok" style={{ marginLeft: 6 }}>current</span>}</div>
              <div className="value">{p.enterprise ? "Custom" : off != null && !p.enterprise ? <><s className="muted small">{gbp(p.monthly_pence)}</s> {gbp(off)}</> : gbp(p.monthly_pence)}<span className="small muted">{p.enterprise ? "" : "/mo"}</span></div>
              <ul className="small" style={{ marginTop: "0.5rem" }}>
                <li>{p.included_minutes} minutes, then {p.overage_pence_per_minute}p/min</li>
                <li>{p.included_numbers} number{p.included_numbers === 1 ? "" : "s"} · {p.included_sms} SMS</li>
                <li>{p.max_assistants} assistant{p.max_assistants === 1 ? "" : "s"} · {p.max_concurrent_calls} concurrent calls</li>
                {p.features.map((f) => <li key={f}>{f}</li>)}
              </ul>
              {p.entitlements.length > 0 && (
                <details className="small" style={{ marginTop: "0.4rem" }}>
                  <summary className="muted">{p.entitlements.length} features included</summary>
                  <ul className="small">{p.entitlements.map((k) => <li key={k}>{label(k)}</li>)}</ul>
                </details>
              )}
              {canManage && !current && (
                p.enterprise ? <a className="small" href="mailto:sales@parlio.co.uk">Talk to us</a> : <button className="primary" style={{ marginTop: "0.6rem" }} onClick={() => choose(p)}>{sub.status === "trialing" || sub.status === "cancelled" ? "Subscribe" : "Switch"}</button>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

function Numbers({ tenant, canManage, numbers, setNumbers, usage, assistants, setMsg, onChange }: {
  tenant: string; canManage: boolean; numbers: TenantNumber[]; setNumbers: (f: (n: TenantNumber[]) => TenantNumber[]) => void; usage: UsageSummary;
  assistants: Assistant[]; setMsg: (m: string | null) => void; onChange: () => Promise<void>;
}) {
  const [found, setFound] = useState<AvailableNumber[] | null>(null);
  const [assistant, setAssistant] = useState(assistants[0]?.assistant_id ?? "");
  const [label, setLabel] = useState("");
  const q = `?tenant_id=${tenant}`;
  const asstName = (id: string) => assistants.find((a) => a.assistant_id === id)?.name ?? id;

  const search = async () => {
    const r = await request<AvailableNumber[]>(`/v1/numbers/search${q}&country=GB&limit=6`);
    if (r.ok) setFound(r.data); else setMsg(`Search failed: ${r.error}`);
  };
  const buy = async (e164: string) => {
    const r = await request<TenantNumber>(`/v1/numbers${q}`, { method: "POST", body: JSON.stringify({ e164, assistant_id: assistant, label: label || null }) });
    if (!r.ok) return setMsg(`Could not provision: ${r.error}`);
    setNumbers((ns) => [...ns, r.data]); setFound(null); setMsg(`${e164} is now routed to ${asstName(assistant)}.`);
    await onChange();
  };
  const release = async (n: TenantNumber) => {
    if (!confirm(`Release ${n.e164}? Callers will no longer reach ${asstName(n.assistant_id)} on it.`)) return;
    if (await del(`/v1/numbers/${n.id}${q}`)) { setNumbers((ns) => ns.filter((x) => x.id !== n.id)); await onChange(); }
  };

  return (
    <>
      <div className="section">
        <h2>Your numbers</h2>
        <p className="hint">{usage.numbers_used} of {usage.numbers_included} included on {usage.plan.name}. Extra numbers are £1/month each.</p>
        {numbers.length === 0 ? <p className="muted small">No numbers yet. Forwarding from your existing line works without one — see <a href="/telephony">Telephony</a>.</p> : (
          <table>
            <thead><tr><th>Number</th><th>Label</th><th>Assistant</th><th>Provider</th><th>Since</th><th /></tr></thead>
            <tbody>
              {numbers.map((n) => (
                <tr key={n.id}>
                  <td><code>{n.e164}</code></td><td>{n.label ?? "—"}</td><td>{asstName(n.assistant_id)}</td>
                  <td>{n.provider === "simulated" ? <span className="pill warn">simulated</span> : n.provider}</td><td>{when(n.created_at)}</td>
                  <td>{canManage && <button onClick={() => release(n)}>Release</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {canManage && (
        <div className="section">
          <h2>Add a UK number</h2>
          <p className="hint">London (020) numbers, routed straight to an assistant. Live carrier purchases need Telnyx regulatory approval; until then numbers are simulated.</p>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
            <select value={assistant} onChange={(e) => setAssistant(e.target.value)}>
              {assistants.map((a) => <option key={a.assistant_id} value={a.assistant_id}>{a.name}</option>)}
            </select>
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label (optional)" style={{ maxWidth: 200 }} />
            <button className="primary" onClick={search}>Search numbers</button>
          </div>
          {found && (
            <div className="chips" style={{ marginTop: "0.8rem" }}>
              {found.map((n) => <a key={n.e164} onClick={() => buy(n.e164)}>{n.e164}</a>)}
              {found.length === 0 && <span className="muted small">No numbers available right now.</span>}
            </div>
          )}
        </div>
      )}
    </>
  );
}

function Latency({ report, tenant }: { report: LatencyReport | null; tenant: string }) {
  const [rep, setRep] = useState(report);
  const [days, setDays] = useState(7);
  const load = async (d: number) => {
    setDays(d);
    const r = await request<LatencyReport>(`/v1/observability/latency?tenant_id=${tenant}&days=${d}`);
    if (r.ok) setRep(r.data);
  };
  if (!rep) return <p className="muted">Latency data unavailable.</p>;
  const o = rep.overall;
  const turnCls = (b: LatencyBucket) => (b.turn_p95_s == null ? "" : b.turn_p95_s > rep.target_turn_s ? "bad" : "ok");
  const perDay = Object.entries(rep.per_day).sort(([a], [b]) => a.localeCompare(b));
  const maxTurn = Math.max(rep.target_turn_s, ...perDay.map(([, b]) => b.turn_p95_s ?? 0));
  return (
    <>
      <div className="chips" style={{ marginBottom: "1rem" }}>
        {[1, 7, 30].map((d) => <a key={d} className={days === d ? "active" : ""} onClick={() => load(d)}>{d === 1 ? "24h" : `${d} days`}</a>)}
      </div>
      <div className="grid">
        <div className="card"><div className="label">Pick-up (answer) p50 / p95</div><div className="value">{ms(o.answer_p50_s)} <span className="muted small">/ {ms(o.answer_p95_s)}</span></div><div className="small muted">time from ring to first word</div></div>
        <div className="card"><div className="label">Turn latency p50 / p95</div><div className="value"><span className={`pill ${turnCls(o)}`} style={{ fontSize: "1.2rem" }}>{ms(o.turn_p50_s)}</span> <span className="muted small">/ {ms(o.turn_p95_s)}</span></div><div className="small muted">target ≤ {ms(rep.target_turn_s)} caller-stops-talking → assistant speaks</div></div>
        <div className="card"><div className="label">Slow calls</div><div className="value">{o.slow_calls}</div><div className="small muted">of {o.calls} with p95 over target</div></div>
        <div className="card"><div className="label">Pipeline averages</div><div className="value small" style={{ fontSize: "1rem" }}>EOU {ms(o.eou_avg_s)} · LLM {ms(o.llm_ttft_avg_s)} · TTS {ms(o.tts_ttfb_avg_s)}</div><div className="small muted">endpointing · first token · first audio</div></div>
      </div>
      <div className="section">
        <h2>Turn p95 by day</h2>
        <p className="hint">Red bars breach the {ms(rep.target_turn_s)} target.</p>
        {perDay.length === 0 ? <p className="muted small">No answered calls in this window.</p> : (
          <div className="bars" style={{ marginBottom: "1.4rem" }}>
            {perDay.map(([d, b]) => (
              <div key={d} className="bar" style={{ height: `${((b.turn_p95_s ?? 0) / maxTurn) * 100}%`, background: turnCls(b) === "bad" ? "var(--bad-fg)" : undefined }} title={`${d}: p95 ${ms(b.turn_p95_s)} (${b.calls} calls)`}><span>{d.slice(8)}</span></div>
            ))}
          </div>
        )}
      </div>
      {Object.keys(rep.per_assistant).length > 0 && (
        <div className="section">
          <h2>By assistant</h2>
          <table>
            <thead><tr><th>Assistant</th><th>Calls</th><th>Answer p50</th><th>Turn p50</th><th>Turn p95</th><th>Slow</th></tr></thead>
            <tbody>
              {Object.entries(rep.per_assistant).map(([id, b]) => (
                <tr key={id}><td>{id}</td><td>{b.calls}</td><td>{ms(b.answer_p50_s)}</td><td>{ms(b.turn_p50_s)}</td><td><span className={`pill ${turnCls(b)}`}>{ms(b.turn_p95_s)}</span></td><td>{b.slow_calls}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">Per-call traces are exported over OpenTelemetry when <code>PARLIO_OTLP_ENDPOINT</code> is set; Prometheus metrics at <code>/metrics</code>.</p>
    </>
  );
}
