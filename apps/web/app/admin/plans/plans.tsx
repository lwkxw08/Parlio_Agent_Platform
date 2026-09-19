"use client";

import { useState } from "react";
import { type Coupon, type Plan, del, gbp, put, request } from "@/lib/api";

const BUILTIN = ["starter", "growth", "scale", "enterprise"];

type PlanForm = Plan & { included_chat_messages?: number; chat_overage_pence?: number };

const capCell = (n: number) => (n === 0 ? "Unlimited" : String(n));

const blankPlan = (): PlanForm => ({
  id: "", name: "", monthly_pence: 9900, included_minutes: 300, overage_pence_per_minute: 15, included_numbers: 1, included_sms: 200,
  sms_overage_pence: 6, max_assistants: 1, max_concurrent_calls: 3, features: [], entitlements: [], enterprise: false, included_chat_messages: 500, chat_overage_pence: 2,
  max_resources: 1, max_sites: 1, max_members: 2,
});
const blankCoupon = (): Coupon & { expires_at?: string | null } => ({ code: "", percent_off: 10, amount_off_pence: null, months: 3, plans: [], expires_at: null });

export default function Plans({ plans: initialPlans, coupons: initialCoupons, catalogue, canEdit, defaultTrialDays }: { plans: Plan[]; coupons: Coupon[]; catalogue: Record<string, string>; canEdit: boolean; defaultTrialDays: number }) {
  const [plans, setPlans] = useState<Plan[]>(initialPlans);
  const [coupons, setCoupons] = useState<Coupon[]>(initialCoupons);
  const [editing, setEditing] = useState<PlanForm | null>(null);
  const [coupon, setCoupon] = useState<ReturnType<typeof blankCoupon> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 6000); };

  const savePlan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    const r = await put<Plan>(`/v1/admin/plans/${editing.id}`, editing);
    if (!r.ok) return flash(r.error);
    setPlans((ps) => (ps.some((p) => p.id === r.data.id) ? ps.map((p) => (p.id === r.data.id ? r.data : p)) : [...ps, r.data]));
    setEditing(null);
    flash(`Saved plan ${r.data.name}`);
  };
  const deletePlan = async (p: Plan) => {
    const builtin = BUILTIN.includes(p.id);
    if (!confirm(builtin ? `Revert ${p.name} to its shipped defaults?` : `Delete plan ${p.name}? Fails if any tenant is on it.`)) return;
    const r = await request<Plan | null>(`/v1/admin/plans/${p.id}`, { method: "DELETE" });
    if (!r.ok) return flash(r.error);
    setPlans((ps) => (r.data ? ps.map((x) => (x.id === p.id ? r.data! : x)) : ps.filter((x) => x.id !== p.id)));
    flash(builtin ? `Reverted ${p.name}` : `Deleted ${p.name}`);
  };
  const saveCoupon = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!coupon) return;
    const body = { ...coupon, code: coupon.code.toUpperCase(), expires_at: coupon.expires_at || null };
    const r = await put<Coupon>(`/v1/admin/coupons/${body.code}`, body);
    if (!r.ok) return flash(r.error);
    setCoupons((cs) => (cs.some((c) => c.code === r.data.code) ? cs.map((c) => (c.code === r.data.code ? r.data : c)) : [...cs, r.data]));
    setCoupon(null);
    flash(`Saved coupon ${r.data.code}`);
  };
  const deleteCoupon = async (c: Coupon) => {
    if (!confirm(`Delete coupon ${c.code}?`)) return;
    if (await del(`/v1/admin/coupons/${c.code}`)) setCoupons((cs) => cs.filter((x) => x.code !== c.code));
  };
  const numField = (k: keyof PlanForm, label: string, min = 0) => editing && (
    <label>{label}<input type="number" min={min} value={Number(editing[k] ?? 0)} onChange={(e) => setEditing({ ...editing, [k]: Number(e.target.value) })} required /></label>
  );

  return (
    <>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      <div className="section">
        <div className="row between">
          <div><h2>Plans</h2><p className="hint">Edits apply to every tenant on the plan from their next invoice. Built-in plans can be edited and reverted; custom plans can be deleted when unused.</p></div>
          {canEdit && <button type="button" className="primary" onClick={() => setEditing(blankPlan())}>New plan</button>}
        </div>
        <table>
          <thead><tr><th>Plan</th><th>Monthly</th><th>Minutes</th><th>Overage /min</th><th>Numbers</th><th>SMS</th><th>Assistants</th><th>Concurrent</th><th>Team members</th><th>Locations</th><th>Users</th><th>Trial</th><th>Functionality</th>{canEdit && <th />}</tr></thead>
          <tbody>
            {plans.map((p) => (
              <tr key={p.id}>
                <td>{p.name} <span className="muted small">{p.id}</span>{p.enterprise && <span className="pill warn" style={{ marginLeft: 6 }}>enterprise</span>}</td>
                <td>{gbp(p.monthly_pence)}</td>
                <td>{p.included_minutes}</td>
                <td>{p.overage_pence_per_minute}p</td>
                <td>{p.included_numbers}</td>
                <td>{p.included_sms} <span className="muted small">+{p.sms_overage_pence}p</span></td>
                <td>{p.max_assistants}</td>
                <td>{p.max_concurrent_calls}</td>
                <td>{capCell(p.max_resources ?? 1)}</td>
                <td>{capCell(p.max_sites ?? 1)}</td>
                <td>{capCell(p.max_members ?? 2)}</td>
                <td>{p.trial_days ?? defaultTrialDays} days{p.trial_days == null && <span className="muted small"> (default)</span>}</td>
                <td className="small">{p.entitlements.length}/{Object.keys(catalogue).length} <span className="muted">· {p.features.join(", ")}</span></td>
                {canEdit && (
                  <td className="row">
                    <button type="button" className="ghost" onClick={() => setEditing({ ...p })}>Edit</button>
                    <button type="button" className="ghost" onClick={() => deletePlan(p)}>{BUILTIN.includes(p.id) ? "Revert" : "Delete"}</button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {editing && (
          <form className="form" onSubmit={savePlan} style={{ marginTop: "1rem" }}>
            <h2>{plans.some((p) => p.id === editing.id) ? `Edit ${editing.name}` : "New plan"}</h2>
            <div className="two">
              <label>Plan id<input value={editing.id} onChange={(e) => setEditing({ ...editing, id: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "") })} required disabled={plans.some((p) => p.id === editing.id)} pattern="[a-z0-9_-]+" /></label>
              <label>Name<input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} required /></label>
              {numField("monthly_pence", "Monthly price (pence)")}
              {numField("included_minutes", "Included minutes")}
              {numField("overage_pence_per_minute", "Overage (pence per minute)")}
              {numField("included_numbers", "Included numbers")}
              {numField("included_sms", "Included SMS")}
              {numField("sms_overage_pence", "SMS overage (pence)")}
              {numField("max_assistants", "Max assistants", 1)}
              {numField("max_concurrent_calls", "Max concurrent calls", 1)}
              {numField("max_resources", "Max team members / bookable calendars (0 = unlimited)")}
              {numField("max_sites", "Max locations (0 = unlimited)")}
              {numField("max_members", "Max dashboard users (0 = unlimited)")}
              <label>Free trial (days)<input type="number" min={0} max={365} placeholder={`Default ${defaultTrialDays}`} value={editing.trial_days ?? ""} onChange={(e) => setEditing({ ...editing, trial_days: e.target.value === "" ? null : Number(e.target.value) })} /></label>
              {numField("included_chat_messages", "Included chat messages")}
              {numField("chat_overage_pence", "Chat overage (pence)")}
            </div>
            <label>Marketing bullets (comma separated, shown on the pricing card)<input value={editing.features.join(", ")} onChange={(e) => setEditing({ ...editing, features: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></label>
            <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
              <div className="row between">
                <div><strong>Included functionality</strong><p className="hint">Enforced by the API for every tenant on this plan (trials get everything; per-tenant feature flags override).</p></div>
                <div className="row">
                  <button type="button" className="ghost" onClick={() => setEditing({ ...editing, entitlements: Object.keys(catalogue) })}>All</button>
                  <button type="button" className="ghost" onClick={() => setEditing({ ...editing, entitlements: [] })}>None</button>
                </div>
              </div>
              <div className="two" style={{ marginBottom: "0.8rem" }}>
                {Object.entries(catalogue).map(([key, desc]) => {
                  const on = editing.entitlements.includes(key);
                  return (
                    <label key={key} className="check" style={{ display: "flex", gap: "0.5rem", alignItems: "center", padding: "0.2rem 0" }}>
                      <input type="checkbox" checked={on} onChange={(e) => setEditing({ ...editing, entitlements: e.target.checked ? [...editing.entitlements, key] : editing.entitlements.filter((k) => k !== key) })} />
                      <span>{desc} <code className="muted small">{key}</code></span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <label className="check"><input type="checkbox" checked={editing.enterprise} onChange={(e) => setEditing({ ...editing, enterprise: e.target.checked })} /> Enterprise (staff-assigned only, hidden from self-serve)</label>
            <div className="row"><button type="submit" className="primary">Save plan</button><button type="button" className="ghost" onClick={() => setEditing(null)}>Cancel</button></div>
          </form>
        )}
      </div>

      <div className="section">
        <div className="row between">
          <div><h2>Coupons</h2><p className="hint">Percent or fixed discount for a number of months (blank = forever), optionally restricted to plans.</p></div>
          {canEdit && <button type="button" className="primary" onClick={() => setCoupon(blankCoupon())}>New coupon</button>}
        </div>
        <table>
          <thead><tr><th>Code</th><th>Discount</th><th>Months</th><th>Plans</th>{canEdit && <th />}</tr></thead>
          <tbody>
            {coupons.map((c) => (
              <tr key={c.code}>
                <td><code>{c.code}</code></td>
                <td>{c.percent_off != null ? `${c.percent_off}%` : c.amount_off_pence != null ? gbp(c.amount_off_pence) : "—"}</td>
                <td>{c.months ?? "forever"}</td>
                <td className="small">{c.plans.length ? c.plans.join(", ") : "any"}</td>
                {canEdit && <td className="row"><button type="button" className="ghost" onClick={() => setCoupon({ ...c })}>Edit</button><button type="button" className="ghost" onClick={() => deleteCoupon(c)}>Delete</button></td>}
              </tr>
            ))}
            {coupons.length === 0 && <tr><td colSpan={5} className="muted">No coupons.</td></tr>}
          </tbody>
        </table>
        {coupon && (
          <form className="form" onSubmit={saveCoupon} style={{ marginTop: "1rem" }}>
            <div className="two">
              <label>Code<input value={coupon.code} onChange={(e) => setCoupon({ ...coupon, code: e.target.value.toUpperCase() })} required disabled={coupons.some((c) => c.code === coupon.code)} /></label>
              <label>Months (blank = forever)<input type="number" min={1} value={coupon.months ?? ""} onChange={(e) => setCoupon({ ...coupon, months: e.target.value === "" ? null : Number(e.target.value) })} /></label>
              <label>Percent off<input type="number" min={1} max={100} value={coupon.percent_off ?? ""} onChange={(e) => setCoupon({ ...coupon, percent_off: e.target.value === "" ? null : Number(e.target.value), amount_off_pence: e.target.value === "" ? coupon.amount_off_pence : null })} /></label>
              <label>Or fixed amount off (pence)<input type="number" min={1} value={coupon.amount_off_pence ?? ""} onChange={(e) => setCoupon({ ...coupon, amount_off_pence: e.target.value === "" ? null : Number(e.target.value), percent_off: e.target.value === "" ? coupon.percent_off : null })} /></label>
              <label>Restrict to plans<select multiple value={coupon.plans} onChange={(e) => setCoupon({ ...coupon, plans: Array.from(e.target.selectedOptions).map((o) => o.value) })}>{plans.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
              <label>Expires<input type="date" value={coupon.expires_at ? coupon.expires_at.slice(0, 10) : ""} onChange={(e) => setCoupon({ ...coupon, expires_at: e.target.value ? `${e.target.value}T23:59:59Z` : null })} /></label>
            </div>
            <div className="row"><button type="submit" className="primary">Save coupon</button><button type="button" className="ghost" onClick={() => setCoupon(null)}>Cancel</button></div>
          </form>
        )}
      </div>
    </>
  );
}
