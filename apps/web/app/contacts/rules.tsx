"use client";

import { useState } from "react";
import { type ContactRules, saveContactRules } from "@/lib/api";

export default function ContactRulesForm({ initial, departments }: { initial: ContactRules; departments: string[] }) {
  const [r, setR] = useState(initial);
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [named, setNamed] = useState(initial.vip_named_accounts.join("\n"));

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = { ...r, vip_named_accounts: named.split(/\n|,/).map((x) => x.trim()).filter(Boolean) };
    const res = await saveContactRules(undefined, body);
    setMsg(res.ok ? "Saved" : res.error);
    if (res.ok) { setR(res.data); setNamed(res.data.vip_named_accounts.join("\n")); }
    setTimeout(() => setMsg(null), 2500);
  };
  const summary = [
    r.auto_promote ? "Prospects become customers automatically" : "No automatic promotion",
    r.vip_min_calls ? `VIP after ${r.vip_min_calls} calls` : null,
    r.vip_min_value_pence ? `VIP over £${Math.round(r.vip_min_value_pence / 100)} spent` : null,
    r.vip_named_accounts.length ? `${r.vip_named_accounts.length} named VIP account(s)` : null,
  ].filter(Boolean).join(" · ");

  return (
    <div className="card" style={{ marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div>
          <h2 style={{ margin: 0 }}>How callers are classified</h2>
          <p className="muted small" style={{ margin: "2px 0 0" }}>{summary}. A status you set by hand always wins over these rules.</p>
        </div>
        <button className="ghost" onClick={() => setOpen((v) => !v)}>{open ? "Close" : "Edit rules"}</button>
      </div>
      {open && (
        <form className="form" onSubmit={save} style={{ marginTop: "0.8rem" }}>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={r.auto_promote} onChange={(e) => setR({ ...r, auto_promote: e.target.checked })} /> Promote Prospect → Customer automatically when…
          </label>
          <div className="two" style={{ paddingLeft: "1.5rem" }}>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><input type="checkbox" disabled={!r.auto_promote} checked={r.promote_on_booking} onChange={(e) => setR({ ...r, promote_on_booking: e.target.checked })} /> an appointment is booked</label>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><input type="checkbox" disabled={!r.auto_promote} checked={r.promote_on_payment} onChange={(e) => setR({ ...r, promote_on_payment: e.target.checked })} /> a payment is made</label>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><input type="checkbox" disabled={!r.auto_promote} checked={r.promote_on_resolved_ticket} onChange={(e) => setR({ ...r, promote_on_resolved_ticket: e.target.checked })} /> a job ticket is resolved</label>
          </div>
          <p className="muted small" style={{ margin: 0 }}>Your CRM (via Connected apps) can also mark a contact as a customer.</p>
          <div className="two">
            <label>VIP after this many calls <input type="number" min={2} value={r.vip_min_calls ?? ""} placeholder="off" onChange={(e) => setR({ ...r, vip_min_calls: e.target.value ? Number(e.target.value) : null })} /></label>
            <label>VIP once lifetime value reaches (£) <input type="number" min={1} value={r.vip_min_value_pence ? r.vip_min_value_pence / 100 : ""} placeholder="off" onChange={(e) => setR({ ...r, vip_min_value_pence: e.target.value ? Math.round(Number(e.target.value) * 100) : null })} /></label>
            <label>Route VIPs to department
              <select value={r.vip_department ?? ""} onChange={(e) => setR({ ...r, vip_department: e.target.value || null })}>
                <option value="">— none —</option>
                {departments.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </label>
          </div>
          <label>Named VIP accounts (one per line — a name or a phone number)
            <textarea value={named} onChange={(e) => setNamed(e.target.value)} placeholder={"Acme Ltd\n07700 900123"} />
          </label>
          <label>How the assistant treats VIPs
            <textarea value={r.vip_instructions} onChange={(e) => setR({ ...r, vip_instructions: e.target.value })} />
          </label>
          <label>How the assistant treats existing customers (optional)
            <textarea value={r.customer_instructions} onChange={(e) => setR({ ...r, customer_instructions: e.target.value })} placeholder="e.g. Don't ask how they heard about us; offer to look up their last job." />
          </label>
          <div><button className="primary">Save rules</button> {msg && <span className="muted small" style={{ marginLeft: 8 }}>{msg}</span>}</div>
        </form>
      )}
    </div>
  );
}
