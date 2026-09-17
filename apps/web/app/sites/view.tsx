"use client";

import Link from "next/link";
import { useState } from "react";
import { type Assistant, type Site, type SiteRef, type SiteRollup, phone, saveSites, secs } from "@/lib/api";
import { Breakdown } from "@/app/breakdown";

const TIMEZONES = ["", "Europe/London", "Europe/Dublin", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Toronto", "Australia/Sydney", "Pacific/Auckland"];

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || `site-${Math.random().toString(36).slice(2, 6)}`;

function blank(): SiteRef {
  return { id: "", name: "", brand_name: "", numbers: [], address: "", timezone: null };
}

export default function SitesView({ assistants, initialSites, rollup, days }: {
  assistants: Assistant[]; initialSites: Site[]; rollup: SiteRollup | null; days: number;
}) {
  const [sites, setSites] = useState<Site[]>(initialSites);
  const [editing, setEditing] = useState<(SiteRef & { assistant_id: string }) | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const defaultAssistant = assistants[0]?.assistant_id ?? "";

  const persist = async (assistant_id: string, next: SiteRef[]) => {
    setSaving(true);
    const r = await saveSites(assistant_id, next);
    setSaving(false);
    if (!r.ok) { setMsg(`Save failed: ${r.error}`); setTimeout(() => setMsg(null), 4000); return false; }
    const a = assistants.find((x) => x.assistant_id === assistant_id);
    const others = sites.filter((s) => s.assistant_id !== assistant_id);
    const mine: Site[] = (r.data.sites ?? []).map((s) => ({
      ...s, assistant_id, assistant_name: a?.name ?? "", departments: [], destinations: a?.transfer.destinations.filter((d) => d.site_id === s.id).length ?? 0,
    }));
    setSites([...others, ...mine]);
    setMsg("Saved"); setTimeout(() => setMsg(null), 2500);
    setEditing(null);
    return true;
  };

  const upsert = (s: SiteRef & { assistant_id: string }) => {
    const { assistant_id, ...ref } = s;
    const rest = sites.filter((x) => x.assistant_id === assistant_id && x.id !== ref.id).map(({ assistant_id: _a, assistant_name: _n, departments: _d, destinations: _c, ...r }) => r);
    void persist(assistant_id, [...rest, ref]);
  };
  const remove = (s: Site) => {
    if (!confirm(`Remove ${s.name}? Its numbers become unassigned and its destinations become shared.`)) return;
    const rest = sites.filter((x) => x.assistant_id === s.assistant_id && x.id !== s.id).map(({ assistant_id: _a, assistant_name: _n, departments: _d, destinations: _c, ...r }) => r);
    void persist(s.assistant_id, rest);
  };

  const summary = rollup?.sites ?? [];
  const byMissed = Object.fromEntries(summary.map((s) => [s.name, s.missed]));
  const byCalls = Object.fromEntries(summary.map((s) => [s.name, s.calls]));

  return (
    <>
      <div className="row between" style={{ alignItems: "baseline", flexWrap: "wrap" }}>
        <h1 style={{ margin: 0 }}>Locations &amp; brands</h1>
        {msg && <span className={`pill ${msg.startsWith("Save failed") ? "bad" : "ok"}`}>{msg}</span>}
      </div>
      <p className="hint">
        Run several branches or brands from one account. Each location has its own phone numbers; when a call comes in on one of them the assistant
        answers with that location&apos;s brand name and details, transfers to that location&apos;s people first, and the call is tagged so you can
        filter <Link href="/calls">Calls</Link> and Analytics by location. Numbers not assigned to any location behave as before.
      </p>

      <div className="section">
        <div className="row between" style={{ alignItems: "baseline" }}>
          <h2 style={{ margin: 0 }}>Your locations</h2>
          <button className="btn" disabled={!defaultAssistant || !!editing} onClick={() => setEditing({ ...blank(), assistant_id: defaultAssistant })}>Add location</button>
        </div>
        {sites.length ? (
          <table>
            <thead><tr><th>Location</th><th>Answers as</th><th>Numbers</th><th>Address</th><th>Timezone</th><th>Team</th><th></th></tr></thead>
            <tbody>
              {sites.map((s) => (
                <tr key={`${s.assistant_id}:${s.id}`}>
                  <td><strong>{s.name}</strong>{assistants.length > 1 && <div className="muted small">{s.assistant_name}</div>}</td>
                  <td>{s.brand_name || <span className="muted">{assistants.find((a) => a.assistant_id === s.assistant_id)?.business_name ?? "—"}</span>}</td>
                  <td className="small">{s.numbers.length ? s.numbers.map(phone).join(", ") : <span className="muted">none yet</span>}</td>
                  <td className="small muted">{s.address || "—"}</td>
                  <td className="small muted">{s.timezone ?? "Same as assistant"}</td>
                  <td className="small">{s.destinations ? `${s.destinations} destination${s.destinations === 1 ? "" : "s"}` : <Link href="/handoff" className="muted">Shared team</Link>}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="ghost" onClick={() => setEditing({ id: s.id, name: s.name, brand_name: s.brand_name, numbers: s.numbers, address: s.address, timezone: s.timezone, assistant_id: s.assistant_id })}>Edit</button>{" "}
                    <button className="ghost" onClick={() => remove(s)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No locations yet — a single-site business doesn&apos;t need any. Add one when you take on a second branch, brand or franchisee.</p>
        )}
        {!!rollup?.unassigned_numbers.length && !!sites.length && (
          <p className="small muted" style={{ marginTop: "0.6rem" }}>
            Numbers that received calls but belong to no location: {rollup.unassigned_numbers.map(phone).join(", ")}. Edit a location to assign them.
          </p>
        )}
        {editing && (
          <SiteForm
            value={editing}
            assistants={assistants}
            taken={sites.filter((s) => s.id !== editing.id).flatMap((s) => s.numbers.map((n) => n.replace("+", "")))}
            saving={saving}
            onCancel={() => setEditing(null)}
            onSave={upsert}
          />
        )}
      </div>

      {!!sites.length && rollup && (
        <div className="section">
          <div className="row between" style={{ alignItems: "baseline", flexWrap: "wrap" }}>
            <h2 style={{ margin: 0 }}>Roll-up · last {days} days</h2>
            <div className="chips">
              {[7, 30, 90, 365].map((d) => (
                <Link key={d} href={`/sites?days=${d}`} className={d === days ? "active" : ""}>{d === 365 ? "1 year" : `${d} days`}</Link>
              ))}
            </div>
          </div>
          <div className="grid">
            <div className="card"><div className="label">Calls across all locations</div><div className="value">{rollup.total_calls}</div></div>
            <div className="card"><div className="label">Best answer rate</div><div className="value">{rollup.best_answer_rate ?? "—"}</div></div>
            <div className="card"><div className="label">Most missed calls</div><div className="value">{rollup.most_missed ?? "—"}</div></div>
          </div>
          <table>
            <thead><tr><th>Location</th><th>Calls</th><th>Share</th><th>Answered</th><th>Missed</th><th>Transferred</th><th>Ticketed</th><th>After hours</th><th>Answer rate</th><th>Avg duration</th><th></th></tr></thead>
            <tbody>
              {summary.map((s) => (
                <tr key={s.site_id ?? "none"}>
                  <td><strong>{s.name}</strong>{s.brand_name && <div className="muted small">{s.brand_name}</div>}</td>
                  <td>{s.calls}</td>
                  <td className="muted">{s.share_pct}%</td>
                  <td>{s.answered}</td>
                  <td className={s.missed ? "bad" : ""}>{s.missed}</td>
                  <td>{s.transferred}</td>
                  <td>{s.ticketed}</td>
                  <td>{s.after_hours}</td>
                  <td>{s.answer_rate == null ? "—" : `${s.answer_rate}%`}</td>
                  <td>{secs(s.avg_duration_s)}</td>
                  <td>{s.site_id ? <Link href={`/calls?site=${s.site_id}`} className="small">Calls</Link> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="grid">
            <Breakdown title="Calls by location" data={byCalls} />
            <Breakdown title="Missed calls by location" data={byMissed} />
          </div>
        </div>
      )}
    </>
  );
}

function SiteForm({ value, assistants, taken, saving, onCancel, onSave }: {
  value: SiteRef & { assistant_id: string }; assistants: Assistant[]; taken: string[]; saving: boolean;
  onCancel: () => void; onSave: (s: SiteRef & { assistant_id: string }) => void;
}) {
  const [s, setS] = useState(value);
  const [numbers, setNumbers] = useState(value.numbers.join("\n"));
  const upd = (p: Partial<typeof s>) => setS({ ...s, ...p });
  const parsed = numbers.split(/[\n,;]+/).map((n) => n.replace(/[\s()-]/g, "")).filter(Boolean).map((n) => (n.startsWith("0") ? `+44${n.slice(1)}` : n.startsWith("+") ? n : `+${n}`));
  const clash = parsed.find((n) => taken.includes(n.replace("+", "")));
  const valid = s.name.trim() && !clash;
  return (
    <form className="form" style={{ marginTop: "1rem", borderTop: "1px solid var(--border)", paddingTop: "1rem" }} onSubmit={(e) => { e.preventDefault(); if (valid) onSave({ ...s, id: s.id || slug(s.name), name: s.name.trim(), brand_name: s.brand_name.trim(), address: s.address.trim(), numbers: parsed, timezone: s.timezone || null }); }}>
      <h3 style={{ margin: 0 }}>{value.name ? `Edit ${value.name}` : "New location"}</h3>
      <div className="two">
        <label>Location name <input value={s.name} onChange={(e) => upd({ name: e.target.value })} placeholder="e.g. Leeds branch" /></label>
        <label>Answers as (brand name) <input value={s.brand_name} onChange={(e) => upd({ brand_name: e.target.value })} placeholder="Leave blank to use the business name" /></label>
      </div>
      <div className="two">
        <label>Phone numbers for this location (one per line)
          <textarea rows={3} value={numbers} onChange={(e) => setNumbers(e.target.value)} placeholder={"+44 113 000 0000\n0113 000 0001"} />
        </label>
        <label>Address (read to callers when asked)
          <textarea rows={3} value={s.address} onChange={(e) => upd({ address: e.target.value })} placeholder="12 High Street, Leeds LS1 1AA" />
        </label>
      </div>
      <div className="two">
        <label>Timezone
          <select value={s.timezone ?? ""} onChange={(e) => upd({ timezone: e.target.value || null })}>
            {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz || "Same as the assistant"}</option>)}
          </select>
        </label>
        {assistants.length > 1 && !value.name && (
          <label>Assistant
            <select value={s.assistant_id} onChange={(e) => upd({ assistant_id: e.target.value })}>
              {assistants.map((a) => <option key={a.assistant_id} value={a.assistant_id}>{a.name} · {a.business_name}</option>)}
            </select>
          </label>
        )}
      </div>
      {clash && <p className="small bad">{phone(clash)} already belongs to another location.</p>}
      <p className="hint">Assign people to this location on the <Link href="/handoff">Transfers</Link> page — destinations without a location are shared by every site.</p>
      <div className="row" style={{ gap: "0.5rem" }}>
        <button className="btn" disabled={!valid || saving}>{saving ? "Saving…" : "Save location"}</button>
        <button type="button" className="ghost" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
