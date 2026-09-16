"use client";

import { useState } from "react";
import { type Contact, patch } from "@/lib/api";

export default function ContactForm({ initial }: { initial: Contact }) {
  const [c, setC] = useState(initial);
  const [saved, setSaved] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const changed = c.status !== initial.status || c.vip !== initial.vip;
    const r = await patch<Contact>(`/v1/contacts/${c.id}`, { name: c.name, email: c.email, notes: c.notes, ...(changed ? { status: c.status, vip: c.vip } : {}) });
    setSaved(r ? "Saved" : "Save failed");
    if (r) setC(r);
    setTimeout(() => setSaved(null), 2000);
  };
  const unpin = async (e: React.MouseEvent) => {
    e.preventDefault();
    const r = await patch<Contact>(`/v1/contacts/${c.id}`, { status_pinned: false });
    if (r) setC(r);
  };
  return (
    <form className="form" onSubmit={save}>
      <div className="two">
        <label>Name <input value={c.name ?? ""} onChange={(e) => setC({ ...c, name: e.target.value || null })} /></label>
        <label>Email <input type="email" value={c.email ?? ""} onChange={(e) => setC({ ...c, email: e.target.value || null })} /></label>
        <label>Number <input value={c.e164} disabled /></label>
        <label>Status
          <select value={c.status} onChange={(e) => setC({ ...c, status: e.target.value as Contact["status"] })}>
            <option value="prospect">Prospect</option><option value="customer">Customer</option><option value="blocked">Blocked</option>
          </select>
        </label>
      </div>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={c.vip} onChange={(e) => setC({ ...c, vip: e.target.checked })} /> VIP — the assistant prioritises and personalises this caller
      </label>
      <label>Notes (the assistant can reference these on future calls)
        <textarea value={c.notes ?? ""} onChange={(e) => setC({ ...c, notes: e.target.value || null })} />
      </label>
      <p className="muted small" style={{ margin: 0 }}>
        {c.status_pinned
          ? <>Status and VIP were set by hand{c.status_source ? ` (${c.status_source})` : ""} — automatic rules won&apos;t change them. <a href="#" onClick={unpin}>Let the rules decide again</a></>
          : <>Status follows your automatic rules{c.status_source ? ` (last change: ${c.status_source.replace("auto:", "")})` : ""}; changing it here pins your choice.</>}
        {c.lifetime_value_pence > 0 && <> · Lifetime value £{Math.round(c.lifetime_value_pence / 100).toLocaleString("en-GB")}</>}
      </p>
      <div><button className="primary">Save</button> {saved && <span className="muted small" style={{ marginLeft: 8 }}>{saved}</span>}</div>
    </form>
  );
}
