"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  type Coupon,
  type Credit,
  type FeatureFlags,
  type Market,
  type Member,
  type TenantLocale,
  fetchMarkets,
  setTenantLocale,
  type Plan,
  type Refund,
  type StaffRole,
  type Subscription,
  type SubscriptionStatus,
  type SupportNote,
  type TenantDetail,
  type TenantLimits,
  type ViewAsGrant,
  VIEW_AS_COOKIE,
  del,
  gbp,
  request,
  when,
} from "@/lib/api";
import { HealthPill, Stat, StatusPill, num, pct } from "../../ui";
import { humanize } from "@/app/breakdown";

const TABS = [["overview", "Overview"], ["subscription", "Subscription"], ["limits", "Limits & flags"], ["people", "People & assets"], ["support", "Support"], ["audit", "Audit"]] as const;
type Tab = (typeof TABS)[number][0];

type Props = { detail: TenantDetail; plans: Plan[]; coupons: Coupon[]; catalogue: Record<string, string>; role: StaffRole | null };

const can = (role: StaffRole | null, ...roles: StaffRole[]) => role === "owner" || (role != null && roles.includes(role));

export default function Tenant({ detail, plans, coupons, catalogue, role }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [msg, setMsg] = useState<string | null>(null);
  const router = useRouter();
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 6000); };
  const s = detail.summary;
  const base = `/v1/admin/tenants/${s.tenant_id}`;
  const canBilling = can(role, "finance", "support");
  const canSupport = can(role, "support");

  const viewAs = async () => {
    const r = await request<ViewAsGrant>(`${base}/view-as`, { method: "POST" });
    if (!r.ok) return flash(r.error);
    const secs = Math.max(60, Math.round((new Date(r.data.expires_at).getTime() - Date.now()) / 1000));
    document.cookie = `${VIEW_AS_COOKIE}=${encodeURIComponent(r.data.token)}; path=/; max-age=${secs}; samesite=lax`;
    router.push(`/?tenant=${s.tenant_id}`);
    router.refresh();
  };

  return (
    <>
      <div className="row between">
        <div>
          <h2 style={{ margin: 0 }}>{s.name} <span className="muted small">{s.tenant_id}</span></h2>
          <div className="row small" style={{ marginTop: 4 }}>
            <StatusPill s={s.status} /><HealthPill h={s.health} /><span className="pill">{humanize(s.plan_name)}</span>
            {s.flags.map((f) => <span key={f} className="pill">{f}</span>)}
          </div>
        </div>
        {role !== null && <button type="button" className="ghost" onClick={viewAs}>View as tenant (read-only)</button>}
      </div>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      <div className="tabs" style={{ marginTop: "0.8rem" }}>
        {TABS.map(([id, label]) => <button key={id} type="button" className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {tab === "overview" && <Overview d={detail} />}
      {tab === "subscription" && <SubscriptionTab d={detail} plans={plans} coupons={coupons} base={base} canEdit={canBilling} flash={flash} />}
      {tab === "limits" && <LimitsTab d={detail} catalogue={catalogue} base={base} canEdit={canSupport} flash={flash} />}
      {tab === "people" && <People d={detail} base={base} canEdit={canSupport} flash={flash} />}
      {tab === "support" && <Support d={detail} base={base} canEdit={can(role, "support", "finance")} flash={flash} />}
      {tab === "audit" && <Audit d={detail} />}
    </>
  );
}

function Overview({ d }: { d: TenantDetail }) {
  const s = d.summary;
  const u = d.usage;
  return (
    <>
      <div className="grid">
        <Stat label="Calls this period" value={num(u.calls)} sub={`${num(u.minutes_used, 0)} of ${u.minutes_included} min · ${num(u.minutes_overage, 0)} overage`} />
        <Stat label="Estimated bill" value={gbp(u.estimated_total_pence)} sub={`base ${gbp(u.base_pence)} · overage ${gbp(u.overage_pence)} · discount −${gbp(u.discount_pence)}`} />
        <Stat label="Credit balance" value={gbp(s.credit_balance_pence)} />
        <Stat label="Gross margin" value={pct(u.gross_margin_pct)} sub={`vendor cost ${gbp(u.vendor_cost_pence)}`} />
        <Stat label="Assistants / members / numbers" value={`${s.assistants} / ${s.members} / ${s.numbers}`} />
        <Stat label="Last call" value={s.last_call_at ? when(s.last_call_at) : "never"} sub={`created ${when(s.created_at)}`} />
      </div>
      {d.notes.filter((n) => n.pinned).map((n) => (
        <div key={n.id} className="banner warn"><strong>Pinned note</strong><span>{n.text}</span><span className="muted small">{n.author} · {when(n.created_at)}</span></div>
      ))}
    </>
  );
}

function SubscriptionTab({ d, plans, coupons, base, canEdit, flash }: { d: TenantDetail; plans: Plan[]; coupons: Coupon[]; base: string; canEdit: boolean; flash: (m: string) => void }) {
  const [sub, setSub] = useState<Subscription>(d.subscription);
  const [credits, setCredits] = useState<Credit[]>(d.credits);
  const [refunds, setRefunds] = useState<Refund[]>(d.refunds);
  const [planId, setPlanId] = useState(sub.plan_id);
  const [coupon, setCoupon] = useState("");
  const [days, setDays] = useState(14);
  const [reason, setReason] = useState("");
  const [creditPence, setCreditPence] = useState(1000);
  const [creditReason, setCreditReason] = useState("");
  const [refundPence, setRefundPence] = useState(1000);
  const [refundReason, setRefundReason] = useState("");
  const [refundInvoice, setRefundInvoice] = useState("");

  const act = async <T,>(path: string, body: unknown, ok: (r: T) => void, done: string) => {
    const r = await request<T>(`${base}${path}`, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
    if (!r.ok) return flash(r.error);
    ok(r.data);
    flash(done);
  };
  const setStatus = (status: SubscriptionStatus) =>
    act<Subscription>("/subscription/status", { status, reason: reason || null }, setSub, `Subscription ${status}`);
  const plan = plans.find((p) => p.id === sub.plan_id);

  return (
    <>
      <div className="grid">
        <Stat label="Plan" value={plan?.name ?? sub.plan_id} sub={plan ? `${gbp(plan.monthly_pence)}/mo · ${plan.included_minutes} min · ${plan.max_concurrent_calls} concurrent` : ""} />
        <Stat label="Status" value={<StatusPill s={sub.status} />} sub={sub.status_reason ?? (sub.trial_ends_at && sub.status === "trialing" ? `trial ends ${when(sub.trial_ends_at)}` : "")} />
        <Stat label="Period" value={`${when(sub.period_start).split(",")[0]} → ${when(sub.period_end).split(",")[0]}`} sub={`provider ${sub.provider}${sub.coupon ? ` · coupon ${sub.coupon}` : ""}`} />
      </div>
      {canEdit && (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
          <form className="section form" onSubmit={(e) => { e.preventDefault(); void act<Subscription>("/subscription/plan", { plan_id: planId, coupon_code: coupon || null }, setSub, `Moved to ${planId}`); }}>
            <h2>Change plan</h2>
            <p className="hint">Enterprise plans can only be assigned here. Proration follows the billing provider.</p>
            <label>Plan
              <select value={planId} onChange={(e) => setPlanId(e.target.value)}>
                {plans.map((p) => <option key={p.id} value={p.id}>{p.name} — {gbp(p.monthly_pence)}/mo{p.enterprise ? " (enterprise)" : ""}</option>)}
              </select>
            </label>
            <label>Coupon (optional)
              <select value={coupon} onChange={(e) => setCoupon(e.target.value)}>
                <option value="">None</option>
                {coupons.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
              </select>
            </label>
            <button type="submit" className="primary">Apply plan</button>
          </form>

          <div className="section form">
            <h2>Lifecycle</h2>
            <p className="hint">Paused/suspended/cancelled tenants stop receiving calls immediately; reactivate to restore.</p>
            <label>Reason (recorded in audit)
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. non-payment, customer request" />
            </label>
            <div className="row">
              {sub.status !== "active" && <button type="button" className="ghost" onClick={() => setStatus("active")}>Reactivate</button>}
              {sub.status !== "paused" && <button type="button" className="ghost" onClick={() => setStatus("paused")}>Pause</button>}
              {sub.status !== "suspended" && <button type="button" className="ghost" onClick={() => setStatus("suspended")}>Suspend</button>}
              {sub.status !== "past_due" && <button type="button" className="ghost" onClick={() => setStatus("past_due")}>Mark past due</button>}
              {sub.status !== "cancelled" && <button type="button" className="danger" onClick={() => { if (confirm("Cancel this subscription?")) void setStatus("cancelled"); }}>Cancel</button>}
            </div>
            {sub.status === "trialing" && (
              <>
                <h2 style={{ marginTop: "0.6rem" }}>Trial</h2>
                <div className="row">
                  <input type="number" min={1} max={365} className="inline" value={days} onChange={(e) => setDays(Number(e.target.value))} />
                  <button type="button" className="ghost" onClick={() => act<Subscription>("/subscription/trial/extend", { days }, setSub, `Trial extended ${days} days`)}>Extend trial</button>
                  <button type="button" className="ghost" onClick={() => act<Subscription>("/subscription/trial/convert", undefined, setSub, "Trial converted to paid")}>Convert to paid</button>
                </div>
              </>
            )}
          </div>

          <form className="section form" onSubmit={(e) => { e.preventDefault(); void act<Credit>("/credits", { pence: creditPence, reason: creditReason }, (c) => setCredits([c, ...credits]), `Credited ${gbp(creditPence)}`); setCreditReason(""); }}>
            <h2>Grant credit</h2>
            <p className="hint">Credits are drawn down against future invoices before any charge.</p>
            <div className="two">
              <label>Amount (pence)<input type="number" min={1} value={creditPence} onChange={(e) => setCreditPence(Number(e.target.value))} required /></label>
              <label>Reason<input value={creditReason} onChange={(e) => setCreditReason(e.target.value)} required placeholder="goodwill, outage, referral…" /></label>
            </div>
            <button type="submit" className="primary">Grant {gbp(creditPence)}</button>
          </form>

          <form className="section form" onSubmit={(e) => { e.preventDefault(); if (!confirm(`Refund ${gbp(refundPence)}?`)) return; void act<Refund>("/refunds", { pence: refundPence, reason: refundReason, invoice_id: refundInvoice || null }, (r) => setRefunds([r, ...refunds]), `Refunded ${gbp(refundPence)}`); setRefundReason(""); }}>
            <h2>Issue refund</h2>
            <p className="hint">Goes back to the card via Stripe when connected; simulated otherwise. Always audited.</p>
            <div className="two">
              <label>Amount (pence)<input type="number" min={1} value={refundPence} onChange={(e) => setRefundPence(Number(e.target.value))} required /></label>
              <label>Invoice (optional)
                <select value={refundInvoice} onChange={(e) => setRefundInvoice(e.target.value)}>
                  <option value="">—</option>
                  {d.invoices.map((i) => <option key={i.id} value={i.id}>{i.id} · {gbp(i.total_pence)}</option>)}
                </select>
              </label>
            </div>
            <label>Reason<input value={refundReason} onChange={(e) => setRefundReason(e.target.value)} required /></label>
            <button type="submit" className="danger">Refund {gbp(refundPence)}</button>
          </form>
        </div>
      )}
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="card">
          <h2>Invoices</h2>
          {d.invoices.length === 0 ? <p className="muted small">No invoices yet.</p> : (
            <table>
              <thead><tr><th>Invoice</th><th>Period</th><th>Total</th><th>Status</th></tr></thead>
              <tbody>{d.invoices.map((i) => (
                <tr key={i.id}>
                  <td>{i.url ? <a href={i.url} target="_blank" rel="noreferrer">{i.id}</a> : i.id}</td>
                  <td className="small">{when(i.period_start).split(",")[0]} → {when(i.period_end).split(",")[0]}</td>
                  <td>{gbp(i.total_pence)}</td>
                  <td><span className={`pill ${i.status === "paid" ? "ok" : i.status === "open" ? "warn" : ""}`}>{humanize(i.status)}</span></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h2>Credits &amp; refunds</h2>
          {credits.length === 0 && refunds.length === 0 ? <p className="muted small">None.</p> : (
            <table>
              <thead><tr><th>When</th><th>Type</th><th>Amount</th><th>Reason</th><th>By</th></tr></thead>
              <tbody>
                {credits.map((c) => <tr key={c.id}><td className="small">{when(c.created_at)}</td><td>credit</td><td>{gbp(c.pence)} <span className="muted small">({gbp(c.remaining_pence)} left)</span></td><td className="small">{c.reason}</td><td className="small">{c.granted_by}</td></tr>)}
                {refunds.map((r) => <tr key={r.id}><td className="small">{when(r.created_at)}</td><td>refund</td><td>−{gbp(r.pence)}</td><td className="small">{r.reason}</td><td className="small">{r.issued_by}</td></tr>)}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}

function LimitsTab({ d, catalogue, base, canEdit, flash }: { d: TenantDetail; catalogue: Record<string, string>; base: string; canEdit: boolean; flash: (m: string) => void }) {
  const [lim, setLim] = useState<TenantLimits>(d.limits);
  const [flags, setFlags] = useState<FeatureFlags>(d.flags);
  const [locale, setLocale] = useState<TenantLocale>(d.locale);
  const [markets, setMarkets] = useState<Market[]>([]);
  useEffect(() => { fetchMarkets().then((m) => setMarkets(m ?? [])); }, []);
  const changeMarket = async (market: string) => {
    const r = await setTenantLocale(d.summary.tenant_id, market);
    if (!r.ok) return flash(r.error);
    setLocale(r.data);
    flash(`Market set to ${market} — assistants move to a ${market} voice on next save`);
  };
  const numOrNull = (v: string) => (v === "" ? null : Number(v));
  const saveLimits = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await request<TenantLimits>(`${base}/limits`, { method: "PUT", body: JSON.stringify({ max_concurrent_calls: lim.max_concurrent_calls, minutes_cap: lim.minutes_cap, rate_limit_per_minute: lim.rate_limit_per_minute, note: lim.note }) });
    if (!r.ok) return flash(r.error);
    setLim(r.data);
    flash("Limits saved");
  };
  const toggle = async (key: string, on: boolean) => {
    const next = { ...flags.flags, [key]: on };
    const r = await request<FeatureFlags>(`${base}/flags`, { method: "PUT", body: JSON.stringify({ flags: next }) });
    if (!r.ok) return flash(r.error);
    setFlags(r.data);
  };
  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
      <form className="section form" onSubmit={saveLimits}>
        <h2>Usage caps &amp; rate limits</h2>
        <p className="hint">Overrides the plan defaults for this tenant only. Leave blank to inherit from the plan.</p>
        <fieldset disabled={!canEdit} style={{ border: 0, padding: 0, margin: 0, display: "contents" }}>
          <label>Max concurrent calls <input type="number" min={1} max={500} value={lim.max_concurrent_calls ?? ""} onChange={(e) => setLim({ ...lim, max_concurrent_calls: numOrNull(e.target.value) })} placeholder={`plan: ${d.usage.plan.max_concurrent_calls}`} /></label>
          <label>Minutes cap per period <input type="number" min={0} value={lim.minutes_cap ?? ""} onChange={(e) => setLim({ ...lim, minutes_cap: numOrNull(e.target.value) })} placeholder="no hard cap (overage billed)" /></label>
          <label>API rate limit (requests / minute) <input type="number" min={10} max={100000} value={lim.rate_limit_per_minute ?? ""} onChange={(e) => setLim({ ...lim, rate_limit_per_minute: numOrNull(e.target.value) })} placeholder="platform default" /></label>
          <label>Note <input value={lim.note ?? ""} onChange={(e) => setLim({ ...lim, note: e.target.value || null })} maxLength={300} /></label>
          {canEdit && <button type="submit" className="primary">Save limits</button>}
        </fieldset>
      </form>
      <div className="section">
        <h2>Feature flags</h2>
        <p className="hint">Per-tenant toggles for gated or beta features. Changes are audited.</p>
        {Object.entries(catalogue).map(([key, desc]) => (
          <label key={key} className="check" style={{ display: "flex", gap: "0.5rem", alignItems: "center", padding: "0.3rem 0" }}>
            <input type="checkbox" disabled={!canEdit} checked={flags.flags[key] ?? false} onChange={(e) => toggle(key, e.target.checked)} />
            <span><code>{key}</code> <span className="muted small">{desc}</span></span>
          </label>
        ))}
        {flags.updated_by && <p className="muted small">Last changed by {flags.updated_by} · {when(flags.updated_at)}</p>}
      </div>
      <div className="section">
        <h2>Market</h2>
        <p className="hint">Where this business operates. Sets which accents lead the voice catalogue and which voice engine applies (Platform admin → Staff → Voice engine).</p>
        <label>Market
          <select value={locale.market} disabled={!canEdit || markets.length === 0} onChange={(e) => changeMarket(e.target.value)}>
            {markets.length === 0 && <option value={locale.market}>{locale.market}</option>}
            {markets.map((m) => <option key={m.code} value={m.code}>{m.name} — {m.accents.join(", ")} voices</option>)}
          </select>
        </label>
        {locale.updated_by && <p className="muted small">Last changed by {locale.updated_by} · {when(locale.updated_at)}</p>}
      </div>
    </div>
  );
}

function People({ d, base, canEdit, flash }: { d: TenantDetail; base: string; canEdit: boolean; flash: (m: string) => void }) {
  const [members, setMembers] = useState<Member[]>(d.members);
  const [numbers, setNumbers] = useState(d.numbers);
  const reset2fa = async (m: Member) => {
    if (!confirm(`Reset two-factor for ${m.email}? They will need to re-enrol.`)) return;
    const r = await request<undefined>(`${base}/members/${m.user_id}/reset-2fa`, { method: "POST" });
    flash(r.ok ? `2FA reset for ${m.email}` : r.error);
  };
  const resend = async (m: Member) => {
    const r = await request<Member>(`${base}/members/${m.user_id}/resend-invite`, { method: "POST" });
    if (!r.ok) return flash(r.error);
    setMembers(members.map((x) => (x.user_id === m.user_id ? r.data : x)));
    flash(`Invite re-sent to ${m.email}`);
  };
  const release = async (id: string, e164: string) => {
    if (!confirm(`Release ${e164}? The tenant will stop receiving calls on it.`)) return;
    const r = await request<undefined>(`${base}/numbers/${id}/release`, { method: "POST" });
    if (!r.ok) return flash(r.error);
    setNumbers(numbers.filter((n) => n.id !== id));
    flash(`Released ${e164}`);
  };
  return (
    <>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="card">
          <h2>Members</h2>
          <table>
            <thead><tr><th>Email</th><th>Role</th><th>Status</th>{canEdit && <th />}</tr></thead>
            <tbody>{members.map((m) => (
              <tr key={m.user_id}>
                <td>{m.email}<div className="small muted">{m.name ?? ""}</div></td>
                <td>{m.role}</td>
                <td><span className={`pill ${m.status === "active" ? "ok" : "warn"}`}>{humanize(m.status)}</span></td>
                {canEdit && (
                  <td className="row">
                    {m.status === "invited" && <button type="button" className="ghost" onClick={() => resend(m)}>Re-send invite</button>}
                    <button type="button" className="ghost" onClick={() => reset2fa(m)}>Reset 2FA</button>
                  </td>
                )}
              </tr>
            ))}</tbody>
          </table>
        </div>
        <div className="card">
          <h2>Assistants</h2>
          {d.assistants.length === 0 ? <p className="muted small">None.</p> : (
            <table>
              <thead><tr><th>Name</th><th>Business</th><th>Version</th></tr></thead>
              <tbody>{d.assistants.map((a) => <tr key={a.id}><td>{a.name}<div className="small muted">{a.id}</div></td><td>{a.business_name}</td><td>{a.version ?? "—"}</td></tr>)}</tbody>
            </table>
          )}
        </div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
        <div className="card">
          <h2>Numbers</h2>
          {numbers.length === 0 ? <p className="muted small">None.</p> : numbers.map((n) => (
            <div key={String(n.id)} className="row between small">
              <span>{String(n.e164)} <span className="muted">{String(n.provider)}</span></span>
              {canEdit && <button type="button" className="danger" onClick={() => release(String(n.id), String(n.e164))}>Release</button>}
            </div>
          ))}
        </div>
        <div className="card">
          <h2>SIP trunks</h2>
          {d.trunks.length === 0 ? <p className="muted small">None.</p> : d.trunks.map((t) => (
            <div key={String(t.id)} className="row between small"><span>{String(t.name)} <span className="muted">{String(t.mode)}</span></span><span className="pill">{String(t.status ?? (t.enabled ? "enabled" : "disabled"))}</span></div>
          ))}
        </div>
        <div className="card">
          <h2>Connectors</h2>
          {d.connectors.length === 0 ? <p className="muted small">None.</p> : d.connectors.map((c) => (
            <div key={String(c.id)} className="row between small"><span>{String(c.name)} <span className="muted">{String(c.provider)}</span></span><span className={`pill ${c.enabled ? "ok" : ""}`}>{c.enabled ? "enabled" : "disabled"}</span></div>
          ))}
        </div>
      </div>
    </>
  );
}

function Support({ d, base, canEdit, flash }: { d: TenantDetail; base: string; canEdit: boolean; flash: (m: string) => void }) {
  const [notes, setNotes] = useState<SupportNote[]>(d.notes);
  const [text, setText] = useState("");
  const [pinned, setPinned] = useState(false);
  const [subject, setSubject] = useState("");
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await request<SupportNote>(`${base}/notes`, { method: "POST", body: JSON.stringify({ text, pinned }) });
    if (!r.ok) return flash(r.error);
    setNotes([r.data, ...notes]);
    setText("");
    setPinned(false);
  };
  const remove = async (id: string) => {
    if (await del(`${base}/notes/${id}`)) setNotes(notes.filter((n) => n.id !== id));
  };
  const gdpr = async (kind: "export" | "erase") => {
    if (!subject) return flash("Enter the caller's phone number first");
    if (kind === "erase" && !confirm(`Erase all data for ${subject} in this tenant? This cannot be undone.`)) return;
    const r = await request<Record<string, unknown>>(`${base}/gdpr/${kind}`, { method: "POST", body: JSON.stringify({ e164: subject }) });
    if (!r.ok) return flash(r.error);
    if (kind === "export") {
      const url = URL.createObjectURL(new Blob([JSON.stringify(r.data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a"); a.href = url; a.download = `gdpr-${d.summary.tenant_id}-${subject}.json`; a.click(); URL.revokeObjectURL(url);
      flash("Subject export downloaded");
    } else flash(`Erased: ${JSON.stringify(r.data)}`);
  };
  return (
    <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
      <div className="section">
        <h2>Support notes</h2>
        <p className="hint">Internal to platform staff; never visible to the tenant.</p>
        {canEdit && (
          <form className="form" onSubmit={add} style={{ marginBottom: "1rem" }}>
            <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a note…" required maxLength={4000} />
            <div className="row between">
              <label className="check"><input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} /> Pin to overview</label>
              <button type="submit" className="primary">Add note</button>
            </div>
          </form>
        )}
        {notes.length === 0 ? <p className="muted small">No notes yet.</p> : notes.map((n) => (
          <div key={n.id} className="list-row" style={{ gridTemplateColumns: "1fr auto" }}>
            <div>
              {n.pinned && <span className="pill warn" style={{ marginRight: 6 }}>pinned</span>}
              {n.text}
              <div className="small muted">{n.author} · {when(n.created_at)}</div>
            </div>
            {canEdit && <button type="button" className="ghost" onClick={() => remove(n.id)}>Delete</button>}
          </div>
        ))}
      </div>
      <div className="section form">
        <h2>GDPR on behalf of tenant</h2>
        <p className="hint">Subject access export / right-to-erasure for a caller, audited under this tenant.</p>
        <label>Caller number (E.164)<input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="+447700900123" disabled={!canEdit} /></label>
        {canEdit && (
          <div className="row">
            <button type="button" className="ghost" onClick={() => gdpr("export")}>Export</button>
            <button type="button" className="danger" onClick={() => gdpr("erase")}>Erase</button>
          </div>
        )}
      </div>
    </div>
  );
}

function Audit({ d }: { d: TenantDetail }) {
  return (
    <table>
      <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Staff?</th></tr></thead>
      <tbody>
        {d.audit.map((e) => (
          <tr key={e.id}>
            <td className="small">{when(e.at)}</td>
            <td>{e.actor}</td>
            <td><code>{e.action}</code></td>
            <td className="small">{e.target ?? "—"}</td>
            <td>{e.meta.platform_staff ? <span className="pill warn">staff</span> : ""}</td>
          </tr>
        ))}
        {d.audit.length === 0 && <tr><td colSpan={5} className="muted">No audit entries.</td></tr>}
      </tbody>
    </table>
  );
}
