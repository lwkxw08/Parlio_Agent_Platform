"use client";

import { useState } from "react";
import { type Contact, patch } from "@/lib/api";

export default function ContactForm({ initial }: { initial: Contact }) {
  const [c, setC] = useState(initial);
  const [saved, setSaved] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await patch<Contact>(`/v1/contacts/${c.id}`, { name: c.name, email: c.email, vip: c.vip, notes: c.notes, status: c.status });
    setSaved(r ? "Saved" : "Save failed");
    if (r) setC(r);
    setTimeout(() => setSaved(null), 2000);
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
      <div><button className="primary">Save</button> {saved && <span className="muted small" style={{ marginLeft: 8 }}>{saved}</span>}</div>
    </form>
  );
}
