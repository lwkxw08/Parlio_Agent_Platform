"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type Assistant,
  type Booking,
  type CalendarConnection,
  type CalendarProvider,
  type Connector,
  type Message,
  type Notification,
  type NotificationRule,
  type NotifyChannel,
  type ProviderInfo,
  type Reminder,
  type ReminderPolicy,
  type SyncJob,
  type SyncLogEntry,
  type TenantApiKey,
  del,
  post,
  put,
  request,
  when,
} from "@/lib/api";
import Connectors from "./connectors";
import { humanize } from "@/app/breakdown";

const EVENTS: [string, string][] = [
  ["ticket.created", "Ticket created"],
  ["ticket.urgent", "Urgent ticket"],
  ["ticket.sla_breached", "SLA breached"],
  ["call.missed", "Missed call"],
  ["call.escalated", "Escalation"],
  ["call.completed", "Call completed"],
  ["lead.qualified", "Qualified lead"],
  ["booking.created", "Booking made"],
  ["sip.registration_changed", "SIP registration changed"],
  ["approval.requested", "Approval requested"],
  ["inbox.handoff", "Inbox: human needed"],
  ["inbox.sla_breached", "Inbox: message unanswered"],
];
const CHANNELS: [NotifyChannel, string, string][] = [
  ["email", "Email", "name@company.co.uk"],
  ["sms", "SMS", "+447700900000"],
  ["slack", "Slack", "https://hooks.slack.com/services/..."],
  ["webhook", "Webhook", "https://example.com/hook"],
];
const TABS = [
  ["connectors", "Connected apps"], ["notifications", "Notifications"], ["sms", "SMS log"], ["calendar", "Calendar & bookings"],
] as const;

type Props = {
  tenant: string;
  tab: string;
  canManage: boolean;
  messages: Message[];
  rules: NotificationRule[];
  log: Notification[];
  connections: CalendarConnection[];
  bookings: Booking[];
  sync: SyncLogEntry[];
  assistants: Assistant[];
  providers: ProviderInfo[];
  payloadFields: string[];
  connectors: Connector[];
  jobs: SyncJob[];
  apiKeys: TenantApiKey[];
  banner: string | null;
  reminderPolicy: ReminderPolicy | null;
  reminders: Reminder[];
};

const statusPill = (s: string) => <span className={`pill ${s === "sent" || s === "connected" || s === "confirmed" ? "ok" : s === "failed" || s === "error" ? "bad" : "warn"}`}>{humanize(s)}</span>;

export default function Integrations(p: Props) {
  const [tab, setTab] = useState(p.tab);
  return (
    <>
      <div className="tabs">
        {TABS.map(([id, label]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {tab === "connectors" && (
        <>
          {p.banner && <p className="hint">{p.banner}</p>}
          <Connectors tenant={p.tenant} canManage={p.canManage} providers={p.providers} payloadFields={p.payloadFields} connectors={p.connectors} jobs={p.jobs} apiKeys={p.apiKeys} />
        </>
      )}
      {tab === "notifications" && <Notifications {...p} />}
      {tab === "sms" && <SmsLog messages={p.messages} assistants={p.assistants} />}
      {tab === "calendar" && <Calendar {...p} />}
    </>
  );
}

function Notifications({ tenant, canManage, rules: initial, log }: Props) {
  const [rules, setRules] = useState(initial);
  const [channel, setChannel] = useState<NotifyChannel>("email");
  const [target, setTarget] = useState("");
  const [name, setName] = useState("Team alerts");
  const [events, setEvents] = useState<string[]>(["ticket.urgent", "lead.qualified"]);
  const [qualifiedOnly, setQualifiedOnly] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);
  const [mobile, setMobile] = useState("");
  const q = `?tenant_id=${tenant}`;
  const summaryRule = rules.find((r) => r.channel === "sms" && r.events.includes("call.completed"));

  const enableSummary = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await request<NotificationRule>(`/v1/notifications/rules${q}`, {
      method: "POST",
      body: JSON.stringify({
        name: "Post-call text summary", channel: "sms", target: mobile,
        events: ["call.completed", "call.missed"], qualified_only: false,
      }),
    });
    if (!r.ok) return setMsg(`Could not enable summaries: ${r.error}`);
    setRules((rs) => [...rs, r.data]); setMobile(""); setMsg("You'll get a text after every call.");
  };

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await request<NotificationRule>(`/v1/notifications/rules${q}`, {
      method: "POST", body: JSON.stringify({ name, channel, target, events, qualified_only: qualifiedOnly }),
    });
    if (!r.ok) return setMsg(`Could not save rule: ${r.error}`);
    setRules((rs) => [...rs, r.data]); setTarget(""); setMsg("Rule saved.");
  };
  const toggle = async (rule: NotificationRule) => {
    const r = await put<NotificationRule>(`/v1/notifications/rules/${rule.id}${q}`, { ...rule, enabled: !rule.enabled });
    if (r.ok) setRules((rs) => rs.map((x) => (x.id === r.data.id ? r.data : x)));
  };
  const remove = async (rule: NotificationRule) => {
    if (!confirm(`Delete "${rule.name}"?`)) return;
    if (await del(`/v1/notifications/rules/${rule.id}${q}`)) setRules((rs) => rs.filter((x) => x.id !== rule.id));
  };
  const test = async (rule: NotificationRule) => {
    const n = await post<Notification>(`/v1/notifications/rules/${rule.id}/test${q}`);
    setMsg(n ? `Test ${n.status} to ${n.target}${n.error ? ` — ${n.error}` : ""}` : "Test failed");
  };

  return (
    <>
      <div className="section">
        <h2>Text me after every call</h2>
        <p className="hint">One SMS per call to the owner's mobile: who rang, what they wanted, the outcome and how to reach them — no need to open the dashboard.</p>
        {summaryRule ? (
          <p className="small">
            {summaryRule.enabled ? <span className="pill ok">On</span> : <span className="pill">Paused</span>} Summaries go to <strong>{summaryRule.target}</strong>.
            {canManage && <> <button onClick={() => toggle(summaryRule)}>{summaryRule.enabled ? "Pause" : "Resume"}</button> <button onClick={() => test(summaryRule)}>Send a test</button></>}
          </p>
        ) : canManage ? (
          <form onSubmit={enableSummary} className="grid" style={{ alignItems: "end" }}>
            <label>Owner's mobile<input value={mobile} onChange={(e) => setMobile(e.target.value)} placeholder="+44 7700 900123" required /></label>
            <div><button type="submit" className="primary">Turn on summaries</button></div>
          </form>
        ) : (
          <p className="muted small">Not enabled. An owner or admin can switch this on.</p>
        )}
      </div>
      <div className="section">
        <h2>Who gets told, and when</h2>
        <p className="hint">Rules fan out call and ticket events to your team. Tick “qualified leads only” to skip spam, wrong numbers and existing customers on call events.</p>
        <table>
          <thead><tr><th>Rule</th><th>Channel</th><th>Target</th><th>Events</th><th>Filter</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td><span className="pill">{humanize(r.channel)}</span></td>
                <td className="small">{r.target}</td>
                <td className="small">{r.events.map((e) => EVENTS.find(([id]) => id === e)?.[1] ?? e).join(", ")}</td>
                <td className="small muted">{r.qualified_only ? "Qualified leads only" : "All"}{r.departments.length ? ` · ${r.departments.join(", ")}` : ""}</td>
                <td>{r.enabled ? <span className="pill ok">on</span> : <span className="pill">off</span>}</td>
                <td className="small">
                  {canManage && <><button onClick={() => test(r)}>Test</button> <button onClick={() => toggle(r)}>{r.enabled ? "Pause" : "Resume"}</button> <button onClick={() => remove(r)}>Delete</button></>}
                </td>
              </tr>
            ))}
            {!rules.length && <tr><td colSpan={7} className="muted">No rules yet — urgent tickets and qualified leads are only visible in the dashboard until you add one.</td></tr>}
          </tbody>
        </table>
      </div>
      {canManage && (
        <form className="section" onSubmit={create}>
          <h2>Add a rule</h2>
          <div className="grid">
            <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
            <label>Channel
              <select value={channel} onChange={(e) => setChannel(e.target.value as NotifyChannel)}>
                {CHANNELS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select>
            </label>
            <label>Send to<input value={target} onChange={(e) => setTarget(e.target.value)} placeholder={CHANNELS.find(([id]) => id === channel)?.[2]} required /></label>
          </div>
          <div className="chips" style={{ marginBottom: "0.8rem" }}>
            {EVENTS.map(([id, label]) => (
              <a key={id} className={events.includes(id) ? "active" : ""} onClick={() => setEvents((ev) => (ev.includes(id) ? ev.filter((x) => x !== id) : [...ev, id]))}>{label}</a>
            ))}
          </div>
          <label className="small check"><input type="checkbox" checked={qualifiedOnly} onChange={(e) => setQualifiedOnly(e.target.checked)} /> Qualified leads only (call events)</label>
          <div style={{ marginTop: "0.8rem" }}><button type="submit" className="primary" disabled={!events.length}>Save rule</button> {msg && <span className="muted small">{msg}</span>}</div>
        </form>
      )}
      <div className="section">
        <h2>Recent notifications</h2>
        <table>
          <thead><tr><th>When</th><th>Event</th><th>Channel</th><th>Target</th><th>Title</th><th>Status</th></tr></thead>
          <tbody>
            {log.slice(0, 50).map((n) => (
              <tr key={n.id}><td className="small">{when(n.created_at)}</td><td className="small">{n.event}</td><td>{n.channel}</td><td className="small">{n.target}</td><td>{n.title}</td><td>{statusPill(n.status)}{n.error && <span className="muted small"> {n.error}</span>}</td></tr>
            ))}
            {!log.length && <tr><td colSpan={6} className="muted">Nothing sent yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SmsLog({ messages, assistants }: { messages: Message[]; assistants: Assistant[] }) {
  const scenarios = assistants.flatMap((a) => a.sms_scenarios.map((s) => ({ ...s, assistant: a.name })));
  return (
    <>
      <div className="section">
        <h2>SMS scenarios</h2>
        <p className="hint">Texts the assistant can send during or after a call. Edit templates in <Link href="/assistant">Assistant Studio</Link>; after-call, missed-call and ticket confirmations are automatic, the rest are offered by the assistant when relevant.</p>
        <table>
          <thead><tr><th>Assistant</th><th>Trigger</th><th>Name</th><th>Template</th><th>Status</th></tr></thead>
          <tbody>
            {scenarios.map((s) => <tr key={`${s.assistant}-${s.id}`}><td>{s.assistant}</td><td><span className="pill">{humanize(s.trigger)}</span></td><td>{s.name}</td><td className="small muted">{s.template}</td><td>{s.enabled ? <span className="pill ok">on</span> : <span className="pill">off</span>}</td></tr>)}
            {!scenarios.length && <tr><td colSpan={5} className="muted">No scenarios configured.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="section">
        <h2>Sent messages</h2>
        <table>
          <thead><tr><th>When</th><th>To</th><th>Trigger</th><th>Message</th><th>Status</th><th>Call</th></tr></thead>
          <tbody>
            {messages.map((m) => (
              <tr key={m.id}>
                <td className="small">{when(m.created_at)}</td><td>{m.to}</td><td className="small">{m.trigger}</td><td className="small">{m.body}</td>
                <td>{statusPill(m.status)}{m.error && <span className="muted small"> {m.error}</span>}</td>
                <td className="small">{m.call_id ? <Link href={`/calls/${m.call_id}`}>view</Link> : "—"}</td>
              </tr>
            ))}
            {!messages.length && <tr><td colSpan={6} className="muted">No messages yet. Sending needs an SMS-capable number configured on the API (PARLIO_SMS_FROM_NUMBER).</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Calendar(p: Props) {
  const { tenant, canManage, connections: initial, bookings, sync } = p;
  const [connections, setConnections] = useState(initial);
  const [provider, setProvider] = useState<CalendarProvider>("google");
  const [url, setUrl] = useState("");
  const [vendor, setVendor] = useState("cal.com");
  const [slot, setSlot] = useState(30);
  const [msg, setMsg] = useState<string | null>(null);
  const q = `?tenant_id=${tenant}`;

  const connect = async (e: React.FormEvent) => {
    e.preventDefault();
    if (provider === "google" || provider === "microsoft") {
      const r = await request<{ url: string }>(`/v1/calendar/oauth/start${q}&provider=${provider}`);
      if (!r.ok) return setMsg(`OAuth not available: ${r.error}`);
      window.location.href = r.data.url;
      return;
    }
    const body = provider === "booking_link"
      ? { provider, name: `${vendor} booking link`, booking_url: url, booking_vendor: vendor }
      : { provider, name: "Demo calendar", slot_minutes: slot };
    const r = await request<CalendarConnection>(`/v1/calendar/connections${q}`, { method: "POST", body: JSON.stringify(body) });
    if (!r.ok) return setMsg(`Could not connect: ${r.error}`);
    setConnections((cs) => [...cs, r.data]); setUrl(""); setMsg("Connected.");
  };
  const remove = async (c: CalendarConnection) => {
    if (!confirm(`Disconnect ${c.name}?`)) return;
    if (await del(`/v1/calendar/connections/${c.id}${q}`)) setConnections((cs) => cs.filter((x) => x.id !== c.id));
  };

  return (
    <>
      <div className="section">
        <h2>Connected calendars</h2>
        <p className="hint">With a calendar connected the assistant checks free slots and books appointments mid-call. With a booking link it texts the caller the link instead.</p>
        <table>
          <thead><tr><th>Name</th><th>Provider</th><th>Account / link</th><th>Slots</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {connections.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td><td><span className="pill">{humanize(c.provider)}</span></td>
                <td className="small">{c.account_email ?? c.booking_url ?? c.calendar_id}</td>
                <td className="small">{c.bookable ? `${c.slot_minutes} min${c.buffer_minutes ? ` + ${c.buffer_minutes} buffer` : ""}` : "link only"}</td>
                <td>{statusPill(c.status)}</td>
                <td>{canManage && <button onClick={() => remove(c)}>Disconnect</button>}</td>
              </tr>
            ))}
            {!connections.length && <tr><td colSpan={6} className="muted">Nothing connected yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {canManage && (
        <form className="section" onSubmit={connect}>
          <h2>Connect</h2>
          <div className="grid">
            <label>Provider
              <select value={provider} onChange={(e) => setProvider(e.target.value as CalendarProvider)}>
                <option value="google">Google Calendar</option>
                <option value="microsoft">Outlook / Microsoft 365</option>
                <option value="booking_link">Booking link (Cal.com, Square, GoHighLevel…)</option>
                <option value="simulated">Demo calendar (no account)</option>
              </select>
            </label>
            {provider === "booking_link" && (
              <>
                <label>Vendor
                  <select value={vendor} onChange={(e) => setVendor(e.target.value)}>
                    <option value="cal.com">Cal.com</option><option value="square">Square Appointments</option><option value="ghl">GoHighLevel</option><option value="other">Other</option>
                  </select>
                </label>
                <label>Booking URL<input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://cal.com/yourbusiness/30min" required /></label>
              </>
            )}
            {provider === "simulated" && <label>Slot length (min)<input type="number" min={5} max={480} value={slot} onChange={(e) => setSlot(Number(e.target.value))} /></label>}
          </div>
          <button type="submit" className="primary">{provider === "google" || provider === "microsoft" ? "Sign in and connect" : "Connect"}</button>
          {msg && <span className="muted small" style={{ marginLeft: 8 }}>{msg}</span>}
          {(provider === "google" || provider === "microsoft") && <p className="hint" style={{ marginTop: "0.6rem" }}>Requires the OAuth client ID/secret to be set on the API for this provider.</p>}
        </form>
      )}
      <div className="section">
        <h2>Bookings</h2>
        <table>
          <thead><tr><th>Start</th><th>Name</th><th>Phone</th><th>Notes</th><th>Status</th><th>Call</th></tr></thead>
          <tbody>
            {bookings.map((b) => <tr key={b.id}><td>{when(b.start)}</td><td>{b.name}</td><td>{b.phone ?? "—"}</td><td className="small muted">{b.notes ?? ""}</td><td>{statusPill(b.status)}</td><td className="small">{b.call_id ? <Link href={`/calls/${b.call_id}`}>view</Link> : "—"}</td></tr>)}
            {!bookings.length && <tr><td colSpan={6} className="muted">No bookings yet.</td></tr>}
          </tbody>
        </table>
      </div>
      <Reminders {...p} />
      <div className="section">
        <h2>Sync log</h2>
        <table>
          <thead><tr><th>When</th><th>Connection</th><th>Action</th><th>Result</th></tr></thead>
          <tbody>
            {sync.slice(0, 50).map((s) => <tr key={s.id}><td className="small">{when(s.at)}</td><td className="small">{s.connection_id}</td><td>{s.action}</td><td>{s.ok ? <span className="pill ok">ok</span> : <span className="pill bad">failed</span>}{s.detail && <span className="muted small"> {s.detail}</span>}</td></tr>)}
            {!sync.length && <tr><td colSpan={4} className="muted">No sync activity yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

const DEFAULT_POLICY: Omit<ReminderPolicy, "tenant_id"> = {
  enabled: false, timezone: "Europe/London", hours_before: [24],
  template: "{business}: reminder of your appointment on {when}. Reply 1 to confirm or 2 to reschedule. Reply STOP to opt out.",
  confirm_reply: "Thanks {name}, you're confirmed for {when}. See you then - {business}",
  reschedule_reply: "No problem {name}, we'll call you shortly to find a new time - {business}",
};
const OFFSETS: [number, string][] = [[48, "2 days before"], [24, "1 day before"], [3, "3 hours before"], [1, "1 hour before"]];

function Reminders({ tenant, canManage, reminderPolicy, reminders }: Props) {
  const [policy, setPolicy] = useState<Omit<ReminderPolicy, "tenant_id">>(reminderPolicy ?? DEFAULT_POLICY);
  const [msg, setMsg] = useState<string | null>(null);
  const q = `?tenant_id=${tenant}`;
  const toggleOffset = (h: number) => setPolicy((p) => ({
    ...p, hours_before: p.hours_before.includes(h) ? p.hours_before.filter((x) => x !== h) : [...p.hours_before, h].sort((a, b) => b - a),
  }));
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await put<ReminderPolicy>(`/v1/reminders/policy${q}`, policy);
    if (!r.ok) return setMsg(`Could not save: ${r.error}`);
    setPolicy(r.data); setMsg(r.data.enabled ? "Reminders on." : "Reminders off.");
  };
  return (
    <>
      <form className="section" onSubmit={save}>
        <h2>SMS appointment reminders</h2>
        <p className="hint">When the assistant books an appointment, the customer gets a text before it. Replying <strong>1</strong> confirms the booking; <strong>2</strong> asks to reschedule, which raises a callback ticket for your team and stops further reminders.</p>
        <label className="small check"><input type="checkbox" disabled={!canManage} checked={policy.enabled} onChange={(e) => setPolicy({ ...policy, enabled: e.target.checked })} /> Send SMS reminders for bookings</label>
        <div className="grid">
          <div>
            <span className="small muted">Send</span>
            {OFFSETS.map(([h, label]) => (
              <label key={h} className="small check"><input type="checkbox" disabled={!canManage} checked={policy.hours_before.includes(h)} onChange={() => toggleOffset(h)} /> {label}</label>
            ))}
          </div>
          <label>Reminder text
            <textarea value={policy.template} disabled={!canManage} onChange={(e) => setPolicy({ ...policy, template: e.target.value })} />
            <span className="small muted">Placeholders: {"{business} {name} {when}"}</span>
          </label>
          <label>Reply when they confirm<textarea value={policy.confirm_reply} disabled={!canManage} onChange={(e) => setPolicy({ ...policy, confirm_reply: e.target.value })} /></label>
          <label>Reply when they want to reschedule<textarea value={policy.reschedule_reply} disabled={!canManage} onChange={(e) => setPolicy({ ...policy, reschedule_reply: e.target.value })} /></label>
        </div>
        {canManage && <button type="submit" className="primary">Save</button>}
        {msg && <span className="muted small" style={{ marginLeft: 8 }}>{msg}</span>}
      </form>
      <div className="section">
        <h2>Reminders sent</h2>
        <table>
          <thead><tr><th>Appointment</th><th>Customer</th><th>Send at</th><th>Status</th><th>Reply</th></tr></thead>
          <tbody>
            {reminders.slice(0, 50).map((r) => (
              <tr key={r.id}><td>{when(r.start)}</td><td>{r.name} <span className="muted small">{r.phone}</span></td><td className="small">{when(r.send_at)}</td><td>{statusPill(r.status)}</td><td className="small muted">{r.reply ?? (r.error ?? "—")}</td></tr>
            ))}
            {!reminders.length && <tr><td colSpan={5} className="muted">No reminders yet — they appear once a booking is made with reminders on.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
