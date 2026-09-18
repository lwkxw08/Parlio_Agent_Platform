"use client";

import { useState } from "react";
import {
  type AssignmentPolicy,
  type BookingMode,
  type CalendarConnection,
  type Resource,
  type ResourceInput,
  type Schedule,
  type SchedulerConfig,
  type SchedulerInput,
  type SchedulerProvider,
  type SchedulerTestResult,
  type ServiceType,
  type Site,
  type TeamSettings,
  createResource,
  deleteResource,
  deleteScheduler,
  fetchResources,
  saveScheduler,
  saveTeamSettings,
  testScheduler,
  updateResource,
} from "@/lib/api";

const DAYS: [string, string][] = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
const POLICIES: { value: AssignmentPolicy; label: string; help: string }[] = [
  { value: "least_loaded", label: "Least loaded that day", help: "Whoever has the fewest jobs on the day gets the booking" },
  { value: "round_robin", label: "Round robin", help: "Takes turns in order" },
  { value: "nearest", label: "Nearest area", help: "Prefers the team member whose postcode areas match the caller's" },
  { value: "preferred", label: "Preferred team member", help: "Returning customers get whoever they had last time when free" },
];
const PROVIDERS: { value: SchedulerProvider; label: string; help: string }[] = [
  { value: "servicem8", label: "ServiceM8", help: "API key from ServiceM8 → Settings → API. Jobs and job activities are created there." },
  { value: "webhook", label: "Your own scheduling system (webhook contract)", help: "Base URL of your endpoint; requests are signed with the shared secret (HMAC-SHA256)." },
  { value: "simulated", label: "Simulated (testing)", help: "In-memory scheduler for trying the flow without a real tool." },
];

const hhmm = (s: string) => s.slice(0, 5);
const defaultHours = (): Schedule => ({
  timezone: "Europe/London",
  always: false,
  hours: Object.fromEntries(["mon", "tue", "wed", "thu", "fri"].map((d) => [d, { open: "08:00", close: "17:00" }])),
});
const blank = (): ResourceInput => ({
  name: "", role: "Team member", skills: [], site_id: null, areas: [], hours: null, active: true, on_call: false,
  connection_id: null, calendar_id: "primary", external_ref: null, phone: null,
});
const csv = (v: string[]) => v.join(", ");
const parseCsv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

type Props = {
  tenant: string;
  initial: Resource[];
  settings: TeamSettings;
  scheduler: SchedulerConfig | null;
  connections: CalendarConnection[];
  services: ServiceType[];
  sites: Site[];
  canManage: boolean;
};

export default function Resources({ tenant, initial, settings: initialSettings, scheduler: initialScheduler, connections, services, sites, canManage }: Props) {
  const [rows, setRows] = useState(initial);
  const [settings, setSettings] = useState(initialSettings);
  const [editing, setEditing] = useState<{ id: string | null; body: ResourceInput } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const bookable = connections.filter((c) => c.bookable);
  const schedulerMode = settings.mode === "scheduler";

  const saveSettings = async (patch: Partial<Pick<TeamSettings, "policy" | "mode" | "emergency_to_on_call">>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    const r = await saveTeamSettings(tenant, { policy: next.policy, mode: next.mode, emergency_to_on_call: next.emergency_to_on_call });
    if (!r.ok) setMsg(`Could not save team settings: ${r.error}`);
  };

  const save = async () => {
    if (!editing) return;
    const body = { ...editing.body, name: editing.body.name.trim() };
    if (!body.name) return setMsg("Give the team member a name");
    const r = editing.id ? await updateResource(tenant, editing.id, body) : await createResource(tenant, body);
    if (!r.ok) return setMsg(`Save failed: ${r.error}`);
    setRows((rs) => (editing.id ? rs.map((x) => (x.id === r.data.id ? r.data : x)) : [...rs, r.data]));
    setEditing(null);
    setMsg(null);
  };

  const toggleActive = async (r: Resource) => {
    const { id, tenant_id: _t, created_at: _c, ...body } = r;
    const u = await updateResource(tenant, id, { ...body, active: !r.active });
    if (u.ok) setRows((rs) => rs.map((x) => (x.id === id ? u.data : x)));
    else setMsg(`Update failed: ${u.error}`);
  };

  const remove = async (r: Resource) => {
    if (!confirm(`Delete ${r.name}? Existing bookings keep their assignee name.`)) return;
    if (await deleteResource(tenant, r.id)) setRows((rs) => rs.filter((x) => x.id !== r.id));
    else setMsg("Delete refused");
  };

  const set = <K extends keyof ResourceInput>(k: K, v: ResourceInput[K]) => setEditing((e) => (e ? { ...e, body: { ...e.body, [k]: v } } : e));
  const setDay = (d: string, open: string | null, close: string | null) =>
    setEditing((e) => {
      if (!e) return e;
      const hours = e.body.hours ?? defaultHours();
      const next = { ...hours.hours };
      if (open && close) next[d] = { open, close };
      else delete next[d];
      return { ...e, body: { ...e.body, hours: { ...hours, hours: next } } };
    });

  return (
    <>
      <div className="section" id="engineers">
        <h2>Team members &amp; resources</h2>
        <p className="hint">
          Add everyone who can be booked. Availability offered to callers is pooled across the team — a slot is offered when anyone who can do that
          service is free — and each booking is assigned to one team member and written to their calendar. With no team members listed, bookings go to the
          primary calendar as before. Once anyone is listed, only team members are booked — add yourself with Calendar ID left as &quot;primary&quot; to keep
          taking jobs on the Integrations calendar.
        </p>
        {msg && <p className="hint warn">{msg}</p>}
        {rows.length > 0 && (
          <table>
            <thead><tr><th>Name</th><th>Role</th><th>Skills</th><th>Areas</th><th>Calendar</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const conn = connections.find((c) => c.id === r.connection_id);
                return (
                  <tr key={r.id} style={{ opacity: r.active ? 1 : 0.55 }}>
                    <td>{r.name}{r.on_call && <span className="pill warn" style={{ marginLeft: 6 }}>On call</span>}</td>
                    <td>{r.role}</td>
                    <td className="small">{r.skills.length ? csv(r.skills) : <span className="muted">Any service</span>}</td>
                    <td className="small">{r.areas.length ? csv(r.areas) : <span className="muted">Anywhere</span>}</td>
                    <td className="small">
                      {schedulerMode ? <span className="muted">{r.external_ref ?? "Scheduling tool"}</span>
                        : conn ? `${conn.name}${r.calendar_id !== "primary" ? ` / ${r.calendar_id}` : ""}`
                        : r.connection_id ? <span className="muted">Unknown calendar</span>
                        : <span className="muted">Primary{r.calendar_id !== "primary" ? ` / ${r.calendar_id}` : ""}</span>}
                    </td>
                    <td><span className={`pill ${r.active ? "ok" : ""}`}>{r.active ? "Active" : "Deactivated"}</span></td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {canManage && (<>
                        <button className="ghost" onClick={() => { const { id, tenant_id: _t, created_at: _c, ...body } = r; setEditing({ id, body }); }}>Edit</button>{" "}
                        <button className="ghost" onClick={() => toggleActive(r)}>{r.active ? "Deactivate" : "Activate"}</button>{" "}
                        <button className="danger" onClick={() => remove(r)}>Delete</button>
                      </>)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {canManage && !editing && <button onClick={() => setEditing({ id: null, body: blank() })} style={{ marginTop: "0.6rem" }}>Add team member</button>}
        {editing && (
          <form className="form" style={{ marginTop: "0.8rem" }} onSubmit={(e) => { e.preventDefault(); save(); }}>
            <h3>{editing.id ? `Edit ${editing.body.name}` : "New team member"}</h3>
            <div className="two">
              <label>Name <input required value={editing.body.name} onChange={(e) => set("name", e.target.value)} /></label>
              <label>Role <input value={editing.body.role} onChange={(e) => set("role", e.target.value)} placeholder="Team member, Plumber, Electrician, Agent…" /></label>
            </div>
            <div className="two">
              <label>Skills / services <span className="muted small">(leave empty for any)</span>
                <input value={csv(editing.body.skills)} onChange={(e) => set("skills", parseCsv(e.target.value))} placeholder={services.length ? services.map((s) => s.name).join(", ") : "Boiler service, Repair"} />
              </label>
              <label>Postcode areas covered <span className="muted small">(outward codes, empty = anywhere)</span>
                <input value={csv(editing.body.areas)} onChange={(e) => set("areas", parseCsv(e.target.value))} placeholder="M1, M2, SK, WA" />
              </label>
            </div>
            <div className="two">
              <label>Location
                <select value={editing.body.site_id ?? ""} onChange={(e) => set("site_id", e.target.value || null)}>
                  <option value="">Any location</option>
                  {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </label>
              <label>Phone <span className="muted small">(optional)</span><input value={editing.body.phone ?? ""} onChange={(e) => set("phone", e.target.value || null)} /></label>
            </div>
            {!schedulerMode ? (
              <div className="two">
                <label>Calendar connection
                  <select value={editing.body.connection_id ?? ""} onChange={(e) => set("connection_id", e.target.value || null)}>
                    <option value="">Primary calendar</option>
                    {bookable.map((c) => <option key={c.id} value={c.id}>{c.name}{c.account_email ? ` (${c.account_email})` : ""}</option>)}
                  </select>
                </label>
                <label>Calendar ID <span className="muted small">(&quot;primary&quot; or a shared calendar&apos;s ID)</span>
                  <input value={editing.body.calendar_id} onChange={(e) => set("calendar_id", e.target.value || "primary")} />
                </label>
              </div>
            ) : (
              <label>Scheduling-tool staff reference <input value={editing.body.external_ref ?? ""} onChange={(e) => set("external_ref", e.target.value || null)} placeholder="Staff UUID in your scheduling tool" /></label>
            )}
            <div style={{ margin: "0.4rem 0" }}>
              <label className="small check"><input type="checkbox" checked={!!editing.body.hours} onChange={(e) => set("hours", e.target.checked ? defaultHours() : null)} /> Own working hours (otherwise the booking rules&apos; hours apply)</label>
              {editing.body.hours && DAYS.map(([d, label]) => {
                const dh = editing.body.hours?.hours[d];
                return (
                  <div key={d} className="small" style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
                    <label className="check" style={{ width: 70 }}><input type="checkbox" checked={!!dh} onChange={(e) => setDay(d, e.target.checked ? "08:00" : null, e.target.checked ? "17:00" : null)} /> {label}</label>
                    {dh && (<>
                      <input type="time" value={hhmm(dh.open)} onChange={(e) => setDay(d, e.target.value, hhmm(dh.close))} />
                      <span className="muted">to</span>
                      <input type="time" value={hhmm(dh.close)} onChange={(e) => setDay(d, hhmm(dh.open), e.target.value)} />
                    </>)}
                  </div>
                );
              })}
            </div>
            <label className="small check"><input type="checkbox" checked={editing.body.on_call} onChange={(e) => set("on_call", e.target.checked)} /> On call — emergency bookings go to this team member first</label>
            <label className="small check"><input type="checkbox" checked={editing.body.active} onChange={(e) => set("active", e.target.checked)} /> Active (bookable)</label>
            <div className="actions">
              <button type="submit">{editing.id ? "Save" : "Add"}</button>
              <button type="button" className="ghost" onClick={() => setEditing(null)}>Cancel</button>
            </div>
          </form>
        )}
      </div>

      <div className="section" id="assignment">
        <h2>How bookings are assigned</h2>
        <div className="two">
          <label>Assignment policy
            <select disabled={!canManage} value={settings.policy} onChange={(e) => saveSettings({ policy: e.target.value as AssignmentPolicy })}>
              {POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
            <span className="hint">{POLICIES.find((p) => p.value === settings.policy)?.help}</span>
          </label>
          <label>Bookings are made in
            <select disabled={!canManage} value={settings.mode} onChange={(e) => saveSettings({ mode: e.target.value as BookingMode })}>
              <option value="calendar">Calendars (Google / Outlook per team member)</option>
              <option value="scheduler">Our scheduling tool (below)</option>
            </select>
          </label>
        </div>
        <label className="small check"><input type="checkbox" disabled={!canManage} checked={settings.emergency_to_on_call} onChange={(e) => saveSettings({ emergency_to_on_call: e.target.checked })} /> Emergency services go to the on-call team member when one is free</label>
      </div>

      <SchedulerPanel tenant={tenant} initial={initialScheduler} canManage={canManage} active={schedulerMode} onImported={(rs) => setRows(rs)} />
    </>
  );
}

function SchedulerPanel({ tenant, initial, canManage, active, onImported }: { tenant: string; initial: SchedulerConfig | null; canManage: boolean; active: boolean; onImported: (rows: Resource[]) => void }) {
  const [cfg, setCfg] = useState<SchedulerConfig | null>(initial);
  const [form, setForm] = useState<SchedulerInput>({
    provider: initial?.provider ?? "servicem8", name: initial?.name ?? "Scheduling tool", base_url: initial?.base_url ?? null,
    secret: null, enabled: initial?.enabled ?? true, read_only_schedule: initial?.read_only_schedule ?? true, default_minutes: initial?.default_minutes ?? 60,
  });
  const [result, setResult] = useState<SchedulerTestResult | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const help = PROVIDERS.find((p) => p.value === form.provider)?.help;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await saveScheduler(tenant, form);
    if (!r.ok) return setMsg(`Save failed: ${r.error}`);
    setCfg(r.data); setForm((f) => ({ ...f, secret: null })); setMsg("Saved"); setResult(null);
  };
  const test = async (importStaff: boolean) => {
    const r = await testScheduler(tenant, importStaff);
    if (!r.ok) return setMsg(`Test failed: ${r.error}`);
    setResult(r.data);
    if (importStaff && r.data.ok) {
      const rs = await fetchResources(tenant);
      if (rs) onImported(rs);
    }
  };
  const remove = async () => {
    if (!confirm("Remove the scheduling tool connection?")) return;
    if (await deleteScheduler(tenant)) { setCfg(null); setResult(null); }
  };

  return (
    <div className="section" id="scheduling-tool">
      <h2>Scheduling tool</h2>
      <p className="hint">
        If your company runs its diary in a job-management tool, connect it here and switch &quot;Bookings are made in&quot; to the scheduling tool.
        The assistant then reads availability from it and creates jobs there instead of calendar events; the Schedule page shows its diary read-only.
        {!active && cfg && <> <strong>Not in use</strong> until the booking mode above is switched.</>}
      </p>
      {cfg && (
        <p className="small">
          <span className={`pill ${cfg.last_error ? "warn" : "ok"}`}>{cfg.last_error ? "Error" : cfg.enabled ? "Connected" : "Disabled"}</span>{" "}
          {PROVIDERS.find((p) => p.value === cfg.provider)?.label} · secret {cfg.has_secret ? "set" : "missing"}
          {cfg.last_sync_at && <> · last checked {new Date(cfg.last_sync_at).toLocaleString("en-GB")}</>}
          {cfg.last_error && <span className="muted"> — {cfg.last_error}</span>}
        </p>
      )}
      {canManage && (
        <form className="form" onSubmit={save}>
          <div className="two">
            <label>Tool
              <select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value as SchedulerProvider })}>
                {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
              <span className="hint">{help}</span>
            </label>
            <label>Name <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          </div>
          <div className="two">
            {form.provider === "webhook" && <label>Base URL <input type="url" value={form.base_url ?? ""} onChange={(e) => setForm({ ...form, base_url: e.target.value || null })} placeholder="https://scheduler.example.com/parlio" /></label>}
            {form.provider !== "simulated" && (
              <label>{form.provider === "servicem8" ? "API key" : "Shared secret"} <span className="muted small">{cfg?.has_secret ? "(leave blank to keep)" : ""}</span>
                <input type="password" value={form.secret ?? ""} onChange={(e) => setForm({ ...form, secret: e.target.value || null })} autoComplete="off" />
              </label>
            )}
            <label>Default job length (min) <input type="number" min={5} max={480} value={form.default_minutes} onChange={(e) => setForm({ ...form, default_minutes: Number(e.target.value) })} /></label>
          </div>
          <label className="small check"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Enabled</label>
          <label className="small check"><input type="checkbox" checked={form.read_only_schedule} onChange={(e) => setForm({ ...form, read_only_schedule: e.target.checked })} /> Schedule page is read-only (the tool owns the diary; reassign/reschedule/cancel there)</label>
          <div className="actions">
            <button type="submit">Save</button>
            {cfg && <button type="button" className="ghost" onClick={() => test(false)}>Test connection</button>}
            {cfg && <button type="button" className="ghost" onClick={() => test(true)}>Import staff as team members</button>}
            {cfg && <button type="button" className="danger" onClick={remove}>Remove</button>}
          </div>
          {msg && <p className="hint">{msg}</p>}
          {result && (
            <p className="small">
              {result.ok ? <>Found {result.staff.length} staff{result.imported ? `, imported ${result.imported}` : ""}: {result.staff.map((s) => s.name).join(", ") || "none"}</>
                : <span className="warn">Connection failed: {result.error}</span>}
            </p>
          )}
          {form.provider === "webhook" && (
            <details className="small" style={{ marginTop: "0.5rem" }}>
              <summary>Webhook contract for your developers</summary>
              <p>Every request carries <code>X-Parlio-Timestamp</code> and <code>X-Parlio-Signature: sha256=HMAC(secret, timestamp + &quot;.&quot; + body)</code>.</p>
              <ul>
                <li><code>GET /resources</code> → <code>[{"{"}id, name, active, skills[], areas[]{"}"}]</code></li>
                <li><code>GET /shifts?start&amp;end</code> → <code>[{"{"}resource_id, start, end{"}"}]</code></li>
                <li><code>GET /availability?start&amp;end&amp;minutes&amp;service&amp;area</code> → <code>[{"{"}start, end, resource_id?{"}"}]</code></li>
                <li><code>POST /jobs</code> {"{"}customer, phone, address, notes, service, minutes, start, end, resource_id, booking_id, call_link{"}"} → <code>{"{"}ref{"}"}</code>; <code>PATCH /jobs/{"{ref}"}</code>; <code>DELETE /jobs/{"{ref}"}</code></li>
                <li><code>GET /jobs?start&amp;end</code> → busy blocks for the Schedule page</li>
                <li>Send changes back to <code>POST /v1/public/scheduler/events/{tenant}</code> with the same signature: <code>{"{"}event: &quot;job.cancelled&quot; | &quot;job.rescheduled&quot;, booking_id | ref, start?, end?{"}"}</code></li>
              </ul>
            </details>
          )}
        </form>
      )}
    </div>
  );
}
