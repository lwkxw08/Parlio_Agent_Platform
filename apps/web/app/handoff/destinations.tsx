"use client";

import { useState } from "react";
import { type Assistant, type Destination, type TransferConfig, put } from "@/lib/api";

const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

function blank(dept: string): Destination {
  return {
    id: `d-${Math.random().toString(36).slice(2, 8)}`,
    name: "",
    department: dept,
    kind: "pstn",
    address: "",
    priority: 0,
    schedule: { timezone: "Europe/London", hours: Object.fromEntries(DAYS.slice(0, 5).map((d) => [d, { open: "09:00", close: "17:30" }])), always: false },
    fallback_id: null,
    on_call: false,
  };
}

export default function Destinations({ assistant }: { assistant: Assistant }) {
  const [cfg, setCfg] = useState<TransferConfig>(assistant.transfer);
  const [editing, setEditing] = useState<Destination | null>(null);
  const [newDept, setNewDept] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const departments = Array.from(new Set(cfg.destinations.map((d) => d.department))).sort();

  const save = async (next: TransferConfig) => {
    setSaving(true);
    const r = await put<TransferConfig>(`/v1/assistants/${assistant.assistant_id}/transfer`, next);
    setSaving(false);
    if (r.ok) { setCfg(r.data); setMsg("Saved"); setEditing(null); }
    else setMsg(`Save failed: ${r.error}`);
    setTimeout(() => setMsg(null), 3000);
  };

  const upsert = (d: Destination) => {
    const rest = cfg.destinations.filter((x) => x.id !== d.id);
    void save({ ...cfg, destinations: [...rest, d].sort((a, b) => a.department.localeCompare(b.department) || a.priority - b.priority) });
  };
  const remove = (id: string) => {
    if (!confirm("Remove this destination?")) return;
    void save({ ...cfg, destinations: cfg.destinations.filter((x) => x.id !== id).map((x) => (x.fallback_id === id ? { ...x, fallback_id: null } : x)) });
  };

  return (
    <div className="section">
      <div className="row between" style={{ alignItems: "baseline" }}>
        <h2 style={{ margin: 0 }}>Departments &amp; destinations</h2>
        {msg && <span className="pill ok">{msg}</span>}
      </div>
      <p className="hint">
        A department is a group of people (or a hunt group) the assistant can transfer to; each destination is one number, SIP address or extension.
        Callers are routed to the department whose description matches their request (e.g. “invoices, payments” → Accounts), then to its destinations in priority order, respecting each person&apos;s hours.
        Mark someone as on-call to receive urgent escalations. Transfer mode and urgent keywords are in <a href="/assistant">Assistant Studio → After hours</a>.
      </p>

      {departments.length === 0 && !editing && <p className="muted small">No departments yet — add one below (e.g. Sales, Support, Bookings, Accounts).</p>}

      {departments.map((dept) => (
        <div key={dept} style={{ marginBottom: "1rem" }}>
          <div className="row between" style={{ alignItems: "center" }}>
            <h3 style={{ margin: "0.5rem 0", textTransform: "capitalize" }}>{dept}</h3>
            <button type="button" className="ghost small" onClick={() => setEditing(blank(dept))}>+ Add person</button>
          </div>
          <input
            className="small"
            style={{ marginBottom: "0.4rem", width: "100%" }}
            placeholder="What this team handles, e.g. invoices, payments, refunds — the assistant uses this to pick the right department"
            defaultValue={cfg.department_notes?.[dept] ?? ""}
            onBlur={(e) => {
              const v = e.target.value.trim();
              if (v === (cfg.department_notes?.[dept] ?? "")) return;
              void save({ ...cfg, department_notes: { ...(cfg.department_notes ?? {}), [dept]: v } });
            }}
          />
          <table>
            <thead><tr><th>Name</th><th>Type</th><th>Number / address</th><th>Priority</th><th>Hours</th><th>On-call</th><th>Fallback</th><th></th></tr></thead>
            <tbody>
              {cfg.destinations.filter((d) => d.department === dept).sort((a, b) => a.priority - b.priority).map((d) => (
                <tr key={d.id}>
                  <td>{d.name}</td>
                  <td>{d.kind}</td>
                  <td><code>{d.address}</code></td>
                  <td>{d.priority}</td>
                  <td>{d.schedule.always ? "24/7" : Object.keys(d.schedule.hours).map((k) => k.slice(0, 3)).join(" ")}</td>
                  <td>{d.on_call ? <span className="pill ok">on-call</span> : "—"}</td>
                  <td>{cfg.destinations.find((x) => x.id === d.fallback_id)?.name ?? "—"}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button type="button" className="ghost small" onClick={() => setEditing(d)}>Edit</button>{" "}
                    <button type="button" className="ghost small" onClick={() => remove(d.id)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      <div className="row" style={{ gap: 8, alignItems: "center", marginTop: "0.5rem" }}>
        <input placeholder="New department name (e.g. Sales)" value={newDept} onChange={(e) => setNewDept(e.target.value)} className="field" style={{ maxWidth: 320 }} />
        <button type="button" className="secondary" disabled={!newDept.trim()} onClick={() => { setEditing(blank(newDept.trim().toLowerCase())); setNewDept(""); }}>Add department</button>
      </div>

      {editing && (
        <DestinationForm
          key={editing.id}
          value={editing}
          others={cfg.destinations.filter((d) => d.id !== editing.id)}
          saving={saving}
          onCancel={() => setEditing(null)}
          onSave={upsert}
        />
      )}
    </div>
  );
}

function DestinationForm({ value, others, saving, onCancel, onSave }: {
  value: Destination; others: Destination[]; saving: boolean; onCancel: () => void; onSave: (d: Destination) => void;
}) {
  const [d, setD] = useState<Destination>(value);
  const upd = (p: Partial<Destination>) => setD({ ...d, ...p });
  const hint = d.kind === "pstn" ? "E.164, e.g. +447700900123" : d.kind === "sip" ? "sip:user@pbx.example.com" : "Extension digits on your BYO trunk, e.g. 201";
  const valid = d.name.trim() && d.address.trim() && d.department.trim();
  return (
    <form className="form" style={{ marginTop: "1rem", borderTop: "1px solid var(--border)", paddingTop: "1rem" }} onSubmit={(e) => { e.preventDefault(); if (valid) onSave({ ...d, name: d.name.trim(), address: d.address.trim(), department: d.department.trim().toLowerCase() }); }}>
      <h3 style={{ margin: 0 }}>{value.name ? `Edit ${value.name}` : "New destination"}</h3>
      <div className="two">
        <label>Name <input value={d.name} onChange={(e) => upd({ name: e.target.value })} placeholder="e.g. Sarah (Sales)" /></label>
        <label>Department <input value={d.department} onChange={(e) => upd({ department: e.target.value })} /></label>
      </div>
      <div className="two">
        <label>Type
          <select value={d.kind} onChange={(e) => upd({ kind: e.target.value as Destination["kind"] })}>
            <option value="pstn">Phone number</option>
            <option value="sip">SIP address</option>
            <option value="extension">PBX extension (BYO trunk)</option>
          </select>
        </label>
        <label>Number / address <input value={d.address} onChange={(e) => upd({ address: e.target.value })} placeholder={hint} /></label>
      </div>
      <div className="two">
        <label>Priority (lower rings first) <input type="number" min={0} max={99} value={d.priority} onChange={(e) => upd({ priority: Number(e.target.value) })} /></label>
        <label>Fallback if unanswered
          <select value={d.fallback_id ?? ""} onChange={(e) => upd({ fallback_id: e.target.value || null })}>
            <option value="">— none (take a message) —</option>
            {others.map((o) => <option key={o.id} value={o.id}>{o.name} · {o.department}</option>)}
          </select>
        </label>
      </div>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={d.on_call} onChange={(e) => upd({ on_call: e.target.checked })} /> On-call for urgent escalations
      </label>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={d.schedule.always} onChange={(e) => upd({ schedule: { ...d.schedule, always: e.target.checked } })} /> Available 24/7
      </label>
      {!d.schedule.always && (
        <div className="hours-grid">
          {DAYS.map((day) => {
            const h = d.schedule.hours[day];
            const set = (v: { open: string; close: string } | null) => {
              const hours = { ...d.schedule.hours };
              if (v) hours[day] = v; else delete hours[day];
              upd({ schedule: { ...d.schedule, hours } });
            };
            return (
              <div key={day} style={{ display: "contents" }}>
                <span style={{ textTransform: "capitalize" }}>{day}</span>
                <input type="time" disabled={!h} value={h?.open.slice(0, 5) ?? ""} onChange={(e) => h && set({ ...h, open: e.target.value })} />
                <input type="time" disabled={!h} value={h?.close.slice(0, 5) ?? ""} onChange={(e) => h && set({ ...h, close: e.target.value })} />
                <button type="button" className="ghost" onClick={() => set(h ? null : { open: "09:00", close: "17:30" })}>{h ? "Closed" : "Open"}</button>
              </div>
            );
          })}
        </div>
      )}
      <div className="row" style={{ gap: 8 }}>
        <button type="submit" disabled={!valid || saving}>{saving ? "Saving…" : "Save destination"}</button>
        <button type="button" className="ghost" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
