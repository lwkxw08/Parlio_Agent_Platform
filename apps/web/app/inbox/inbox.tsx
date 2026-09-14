"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type CannedReply,
  type Channel,
  type ChatWidgetInfo,
  type InboxMessage,
  type InboxStats,
  type InboxThread,
  type LiveMessage,
  type Member,
  type ThreadFilters,
  type ThreadStatus,
  type WhatsAppInfo,
  createCanned,
  deleteCanned,
  fetchInboxStats,
  fetchThread,
  fetchThreads,
  liveSocketUrl,
  noteThread,
  patchThread,
  patchWidget,
  replyThread,
  saveWhatsApp,
  updateCanned,
  when,
} from "@/lib/api";

type Props = {
  tenant: string;
  me: string;
  canReply: boolean;
  isAdmin: boolean;
  initialThreads: InboxThread[];
  initialStats: InboxStats;
  initialCanned: CannedReply[];
  members: Member[];
  initialWidget: ChatWidgetInfo | null;
  initialWhatsApp: WhatsAppInfo | null;
  initialSelected: string | null;
};

type View = "threads" | "canned" | "channels";

const CHANNELS: { id: Channel; label: string }[] = [
  { id: "call", label: "Calls" },
  { id: "voicemail", label: "Voicemail" },
  { id: "sms", label: "SMS" },
  { id: "whatsapp", label: "WhatsApp" },
  { id: "webchat", label: "Web chat" },
];
const CHANNEL_LABEL: Record<Channel, string> = { call: "Call", voicemail: "Voicemail", sms: "SMS", whatsapp: "WhatsApp", webchat: "Web chat" };
const STATUS_PILL: Record<ThreadStatus, string> = { open: "ok", waiting: "warn", closed: "" };

function ago(iso: string, now: number): string {
  const s = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function slaLabel(t: InboxThread, now: number): { text: string; cls: string } | null {
  if (t.status === "closed" || !t.sla_due_at) return null;
  if (t.sla_breached) return { text: "SLA breached", cls: "bad" };
  const left = Math.floor((new Date(t.sla_due_at).getTime() - now) / 60000);
  if (left < 0) return { text: "SLA overdue", cls: "bad" };
  return { text: `${left}m left`, cls: left <= 5 ? "warn" : "" };
}

export default function Inbox(p: Props) {
  const [view, setView] = useState<View>("threads");
  const [threads, setThreads] = useState<Map<string, InboxThread>>(() => new Map(p.initialThreads.map((t) => [t.id, t])));
  const [stats, setStats] = useState<InboxStats>(p.initialStats);
  const [filters, setFilters] = useState<ThreadFilters>({});
  const [selected, setSelected] = useState<string | null>(p.initialSelected ?? p.initialThreads[0]?.id ?? null);
  const [messages, setMessages] = useState<InboxMessage[]>([]);
  const [canned, setCanned] = useState<CannedReply[]>(p.initialCanned);
  const [socket, setSocket] = useState<"connecting" | "live" | "offline">("connecting");
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const timelineRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(t);
  }, []);

  const reload = useCallback(async (f: ThreadFilters) => {
    const [list, st] = await Promise.all([fetchThreads(p.tenant, f), fetchInboxStats(p.tenant)]);
    if (list) setThreads(new Map(list.map((t) => [t.id, t])));
    if (st) setStats(st);
  }, [p.tenant]);

  useEffect(() => { void reload(filters); }, [filters, reload]);

  // Selected thread: load timeline and mark read.
  useEffect(() => {
    if (!selected) { setMessages([]); return; }
    let cancelled = false;
    void (async () => {
      const d = await fetchThread(p.tenant, selected);
      if (cancelled || !d) return;
      setMessages(d.messages);
      setThreads((prev) => new Map(prev).set(d.thread.id, d.thread));
      if (d.thread.unread > 0 && p.canReply) {
        const t = await patchThread(p.tenant, selected, { read: true });
        if (t && !cancelled) {
          setThreads((prev) => new Map(prev).set(t.id, t));
          setStats((s) => ({ ...s, unread: Math.max(0, s.unread - 1) }));
        }
      }
    })();
    return () => { cancelled = true; };
  }, [selected, p.tenant, p.canReply]);

  useEffect(() => {
    timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight });
  }, [messages]);

  const onMessage = useCallback((m: LiveMessage) => {
    if (m.type !== "inbox.thread" && m.type !== "inbox.message") return;
    const t = m.payload.thread as InboxThread | undefined;
    const msg = m.payload.message as InboxMessage | undefined;
    if (t) setThreads((prev) => new Map(prev).set(t.id, t));
    if (msg && msg.thread_id === selected) {
      setMessages((prev) => (prev.some((x) => x.id === msg.id) ? prev : [...prev, msg]));
    }
    void fetchInboxStats(p.tenant).then((s) => s && setStats(s));
  }, [p.tenant, selected]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const connect = async () => {
      setSocket("connecting");
      ws = new WebSocket(await liveSocketUrl(p.tenant));
      ws.onopen = () => { setSocket("live"); retry = 1000; };
      ws.onmessage = (e) => onMessage(JSON.parse(String(e.data)) as LiveMessage);
      ws.onclose = () => {
        setSocket("offline");
        if (!closed) { timer = setTimeout(connect, retry); retry = Math.min(retry * 2, 15000); }
      };
      ws.onerror = () => ws?.close();
    };
    void connect();
    return () => { closed = true; clearTimeout(timer); ws?.close(); };
  }, [p.tenant, onMessage]);

  const list = useMemo(() => {
    const out = [...threads.values()].filter((t) => {
      if (filters.status && t.status !== filters.status) return false;
      if (!filters.status && t.status === "closed") return false;
      if (filters.channel && t.channel !== filters.channel) return false;
      if (filters.unassigned && t.assigned_to) return false;
      if (filters.assigned_to && t.assigned_to !== filters.assigned_to) return false;
      if (filters.unread_only && !t.unread) return false;
      if (filters.q) {
        const q = filters.q.toLowerCase();
        if (![t.identity, t.contact_name ?? "", t.last_preview, t.subject ?? ""].some((s) => s.toLowerCase().includes(q))) return false;
      }
      return true;
    });
    out.sort((a, b) => Number(b.sla_breached) - Number(a.sla_breached) || b.last_message_at.localeCompare(a.last_message_at));
    return out;
  }, [threads, filters]);

  const current = selected ? threads.get(selected) ?? null : null;

  const update = async (body: Parameters<typeof patchThread>[2]) => {
    if (!current) return;
    setBusy(true);
    const t = await patchThread(p.tenant, current.id, body);
    setBusy(false);
    if (t) setThreads((prev) => new Map(prev).set(t.id, t));
    void fetchInboxStats(p.tenant).then((s) => s && setStats(s));
  };

  const send = async (text: string, asNote: boolean) => {
    if (!current || !text.trim()) return;
    setBusy(true);
    const m = asNote ? await noteThread(p.tenant, current.id, text.trim()) : await replyThread(p.tenant, current.id, text.trim());
    setBusy(false);
    if (m) {
      setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
      const d = await fetchThread(p.tenant, current.id);
      if (d) setThreads((prev) => new Map(prev).set(d.thread.id, d.thread));
    }
    return m;
  };

  return (
    <>
      <div className="grid">
        <Stat label="Open" value={stats.open} onClick={() => setFilters({ status: "open" })} />
        <Stat label="Waiting on us" value={stats.waiting} onClick={() => setFilters({ status: "waiting" })} />
        <Stat label="Unread" value={stats.unread} onClick={() => setFilters({ unread_only: true })} />
        <Stat label="Unassigned" value={stats.unassigned} onClick={() => setFilters({ unassigned: true })} />
        <Stat label="SLA breached" value={stats.breached} tone={stats.breached ? "bad" : undefined} />
        <div className="card"><div className="label">Live updates</div><div className="value"><span className={`pill ${socket === "live" ? "ok" : socket === "offline" ? "bad" : "warn"}`}>{socket}</span></div></div>
      </div>

      <div className="tabs">
        <button className={view === "threads" ? "active" : ""} onClick={() => setView("threads")}>Conversations</button>
        <button className={view === "canned" ? "active" : ""} onClick={() => setView("canned")}>Canned replies ({canned.length})</button>
        {p.isAdmin && <button className={view === "channels" ? "active" : ""} onClick={() => setView("channels")}>Channels</button>}
      </div>

      {view === "threads" && (
        <div className="inbox-layout">
          <div className="section inbox-list">
            <div className="filters inbox-filters">
              <input placeholder="Search name, number or text" value={filters.q ?? ""} onChange={(e) => setFilters({ ...filters, q: e.target.value || undefined })} />
              <select value={filters.status ?? ""} onChange={(e) => setFilters({ ...filters, status: (e.target.value || undefined) as ThreadStatus | undefined })}>
                <option value="">Open & waiting</option>
                <option value="open">Open</option>
                <option value="waiting">Waiting on us</option>
                <option value="closed">Closed</option>
              </select>
              <select value={filters.channel ?? ""} onChange={(e) => setFilters({ ...filters, channel: (e.target.value || undefined) as Channel | undefined })}>
                <option value="">All channels</option>
                {CHANNELS.map((c) => <option key={c.id} value={c.id}>{c.label}{stats.by_channel[c.id] ? ` (${stats.by_channel[c.id]})` : ""}</option>)}
              </select>
              <select
                value={filters.unassigned ? "__none" : filters.assigned_to ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  setFilters({ ...filters, unassigned: v === "__none" || undefined, assigned_to: v && v !== "__none" ? v : undefined });
                }}
              >
                <option value="">Anyone</option>
                <option value="__none">Unassigned</option>
                <option value={p.me}>Mine</option>
                {p.members.filter((m) => m.email !== p.me).map((m) => <option key={m.user_id} value={m.email}>{m.name ?? m.email}</option>)}
              </select>
              <label className="row small"><input type="checkbox" checked={!!filters.unread_only} onChange={(e) => setFilters({ ...filters, unread_only: e.target.checked || undefined })} /> Unread only</label>
            </div>
            {!list.length && (
              <p className="muted">
                No conversations match. Messages from calls, voicemail, SMS, WhatsApp and the web chat widget all land here — enable channels under <button className="linklike" onClick={() => setView("channels")}>Channels</button>.
              </p>
            )}
            <div className="inbox-threads">
              {list.map((t) => {
                const sla = slaLabel(t, now);
                return (
                  <button key={t.id} className={`live-row ${t.id === selected ? "active" : ""} ${t.unread ? "unread" : ""}`} onClick={() => setSelected(t.id)}>
                    <span className="row between">
                      <strong>{t.contact_name ?? (t.channel === "webchat" ? "Website visitor" : t.identity)}</strong>
                      <span className="muted small">{ago(t.last_message_at, now)}</span>
                    </span>
                    <span className="row small">
                      <span className="pill">{CHANNEL_LABEL[t.channel]}</span>
                      <span className={`pill ${STATUS_PILL[t.status]}`}>{t.status}</span>
                      {t.unread > 0 && <span className="pill accent">{t.unread} new</span>}
                      {!t.ai_enabled && <span className="pill warn">{t.handoff_department ? `for ${t.handoff_department}` : "human"}</span>}
                      {t.callback_ticket_id && <span className="pill">callback ticket</span>}
                      {sla && <span className={`pill ${sla.cls}`}>{sla.text}</span>}
                      {t.assigned_to && <span className="muted">→ {t.assigned_to === p.me ? "you" : t.assigned_to.split("@")[0]}</span>}
                    </span>
                    <span className="muted small live-last">{t.last_direction === "out" ? "↩ " : ""}{t.last_preview || "…"}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="section inbox-detail">
            {!current ? (
              <p className="muted">Select a conversation.</p>
            ) : (
              <ThreadPane
                t={current}
                me={p.me}
                members={p.members}
                messages={messages}
                canned={canned}
                canReply={p.canReply}
                busy={busy}
                now={now}
                timelineRef={timelineRef}
                onUpdate={update}
                onSend={send}
              />
            )}
          </div>
        </div>
      )}

      {view === "canned" && (
        <CannedPane tenant={p.tenant} canned={canned} setCanned={setCanned} canEdit={p.canReply} />
      )}

      {view === "channels" && p.isAdmin && (
        <ChannelsPane tenant={p.tenant} widget={p.initialWidget} whatsapp={p.initialWhatsApp} />
      )}
    </>
  );
}

function Stat({ label, value, tone, onClick }: { label: string; value: number; tone?: string; onClick?: () => void }) {
  return (
    <div className={`card ${onClick ? "clickable" : ""}`} onClick={onClick} role={onClick ? "button" : undefined}>
      <div className="label">{label}</div>
      <div className="value" style={tone === "bad" && value ? { color: "var(--bad-fg)" } : undefined}>{value}</div>
    </div>
  );
}

type PaneProps = {
  t: InboxThread;
  me: string;
  members: Member[];
  messages: InboxMessage[];
  canned: CannedReply[];
  canReply: boolean;
  busy: boolean;
  now: number;
  timelineRef: React.RefObject<HTMLDivElement | null>;
  onUpdate: (body: Parameters<typeof patchThread>[2]) => Promise<void>;
  onSend: (text: string, asNote: boolean) => Promise<InboxMessage | null | undefined>;
};

function ThreadPane({ t, me, members, messages, canned, canReply, busy, now, timelineRef, onUpdate, onSend }: PaneProps) {
  const [text, setText] = useState("");
  const [asNote, setAsNote] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const textChannel = t.channel === "sms" || t.channel === "whatsapp" || t.channel === "webchat";
  const sla = slaLabel(t, now);

  useEffect(() => { setText(""); setAsNote(false); }, [t.id]);

  const insertCanned = (c: CannedReply) => {
    setText((cur) => (cur ? `${cur}\n${c.body}` : c.body));
    textRef.current?.focus();
  };
  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit(); }
    // "/shortcut" + Tab expands a canned reply.
    if (e.key === "Tab" && text.startsWith("/")) {
      const c = canned.find((x) => x.shortcut && `/${x.shortcut}` === text.trim());
      if (c) { e.preventDefault(); setText(c.body); }
    }
  };
  const submit = async () => {
    const m = await onSend(text, asNote || !textChannel);
    if (m) setText("");
  };

  return (
    <>
      <div className="row between inbox-head">
        <div>
          <h2 style={{ margin: 0 }}>{t.contact_name ?? (t.channel === "webchat" ? "Website visitor" : t.identity)}</h2>
          <div className="muted small row">
            <span className="pill">{CHANNEL_LABEL[t.channel]}</span>
            {t.channel !== "webchat" && <span>{t.identity}</span>}
            {t.contact_id && <Link href={`/contacts?tenant=${t.tenant_id}&q=${encodeURIComponent(t.identity)}`}>contact</Link>}
            <span>· {t.message_count} messages</span>
            {sla && <span className={`pill ${sla.cls}`}>{sla.text}</span>}
          </div>
        </div>
        {canReply && (
          <div className="actions row">
            <select className="field" value={t.assigned_to ?? ""} disabled={busy} onChange={(e) => void onUpdate(e.target.value ? { assigned_to: e.target.value } : { clear_assignee: true })}>
              <option value="">Unassigned</option>
              {!members.some((m) => m.email === me) && <option value={me}>{me}</option>}
              {members.map((m) => <option key={m.user_id} value={m.email}>{m.email === me ? "Me" : m.name ?? m.email}</option>)}
            </select>
            {textChannel && (
              <button disabled={busy} title={t.ai_enabled ? "Pause the assistant so only humans reply" : "Let the assistant reply again"} onClick={() => void onUpdate({ ai_enabled: !t.ai_enabled })}>
                {t.ai_enabled ? "Pause AI" : "Resume AI"}
              </button>
            )}
            {t.status !== "closed" ? (
              <button disabled={busy} onClick={() => void onUpdate({ status: "closed" })}>Close</button>
            ) : (
              <button disabled={busy} onClick={() => void onUpdate({ status: "open" })}>Reopen</button>
            )}
            {t.unread > 0 && <button disabled={busy} onClick={() => void onUpdate({ read: true })}>Mark read</button>}
          </div>
        )}
      </div>

      <div className="live-transcript inbox-timeline transcript" ref={timelineRef}>
        {!messages.length && <p className="muted">No messages yet.</p>}
        {messages.map((m) => {
          const cls = m.direction === "note" ? "note" : m.author === "system" ? "supervisor" : m.direction === "in" ? "user" : "assistant";
          const who = m.direction === "note" ? `Internal note · ${m.author_name ?? "agent"}` : m.author === "contact" ? t.contact_name ?? "Contact" : m.author === "ai" ? m.author_name ?? "Assistant" : m.author === "agent" ? m.author_name ?? "Agent" : "System";
          return (
            <div key={m.id} className={`msg ${cls}`}>
              <span className="who small muted">{who} · {when(m.created_at)}{m.status === "failed" ? ` · failed${m.error ? `: ${m.error}` : ""}` : m.status === "skipped" ? " · not sent (no provider)" : ""}</span>
              <span style={{ whiteSpace: "pre-wrap" }}>{m.text}</span>
              {(m.call_id || m.ticket_id) && (
                <span className="small row" style={{ marginTop: "0.3rem" }}>
                  {m.call_id && <Link href={`/calls/${m.call_id}`}>View call</Link>}
                  {m.ticket_id && <Link href={`/tickets?tenant=${t.tenant_id}`}>Ticket {m.ticket_id}</Link>}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {canReply && (
        <div className="form inbox-composer">
          {canned.length > 0 && !asNote && textChannel && (
            <div className="chips">
              {canned.slice(0, 8).map((c) => <a key={c.id} onClick={() => insertCanned(c)} title={c.body}>{c.title}</a>)}
            </div>
          )}
          <textarea
            ref={textRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            placeholder={asNote || !textChannel ? "Internal note (only your team sees this)" : `Reply by ${CHANNEL_LABEL[t.channel]} — Ctrl/⌘+Enter to send, /shortcut + Tab for canned replies`}
            disabled={busy}
          />
          <div className="row between">
            <label className="row small">
              {textChannel ? (
                <><input type="checkbox" checked={asNote} onChange={(e) => setAsNote(e.target.checked)} /> Internal note</>
              ) : (
                <span className="muted">Calls and voicemail take notes only — call back from the <Link href={`/contacts?tenant=${t.tenant_id}`}>contact</Link> or <Link href={`/outbound?tenant=${t.tenant_id}`}>Outbound</Link>.</span>
              )}
            </label>
            <div className="row">
              {t.ai_enabled && textChannel && !asNote && <span className="muted small">Sending pauses the assistant on this thread.</span>}
              <button className="primary" disabled={busy || !text.trim()} onClick={() => void submit()}>{asNote || !textChannel ? "Add note" : "Send"}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function CannedPane({ tenant, canned, setCanned, canEdit }: { tenant: string; canned: CannedReply[]; setCanned: (c: CannedReply[]) => void; canEdit: boolean }) {
  const [editing, setEditing] = useState<CannedReply | null>(null);
  const [title, setTitle] = useState("");
  const [shortcut, setShortcut] = useState("");
  const [body, setBody] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const start = (c: CannedReply | null) => {
    setEditing(c);
    setTitle(c?.title ?? "");
    setShortcut(c?.shortcut ?? "");
    setBody(c?.body ?? "");
    setErr(null);
  };
  const save = async () => {
    const payload = { title: title.trim(), shortcut: shortcut.trim().replace(/^\//, "") || undefined, body: body.trim() };
    if (!payload.title || !payload.body) { setErr("Title and text are required."); return; }
    if (editing) {
      const r = await updateCanned(tenant, editing.id, payload);
      if (!r.ok) { setErr(r.error ?? "save failed"); return; }
      setCanned(canned.map((c) => (c.id === r.data.id ? r.data : c)));
    } else {
      const c = await createCanned(tenant, payload);
      if (!c) { setErr("save failed"); return; }
      setCanned([...canned, c].sort((a, b) => a.title.localeCompare(b.title)));
    }
    start(null);
  };
  const remove = async (c: CannedReply) => {
    if (!confirm(`Delete "${c.title}"?`)) return;
    if (await deleteCanned(tenant, c.id)) setCanned(canned.filter((x) => x.id !== c.id));
  };

  return (
    <div className="two-col">
      <div className="section">
        <h2>Canned replies</h2>
        <p className="hint">Reusable answers for the team. Type <code>/shortcut</code> then Tab in the composer to expand one.</p>
        {!canned.length && <p className="muted">None yet.</p>}
        <table>
          <tbody>
            {canned.map((c) => (
              <tr key={c.id}>
                <td><strong>{c.title}</strong>{c.shortcut && <span className="muted small"> /{c.shortcut}</span>}<br /><span className="muted small">{c.body.length > 120 ? `${c.body.slice(0, 120)}…` : c.body}</span></td>
                {canEdit && (
                  <td className="actions" style={{ whiteSpace: "nowrap" }}>
                    <button onClick={() => start(c)}>Edit</button> <button onClick={() => void remove(c)}>Delete</button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canEdit && (
        <form className="section form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <h2>{editing ? "Edit reply" : "New reply"}</h2>
          <div className="two">
            <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} required /></label>
            <label>Shortcut<input value={shortcut} onChange={(e) => setShortcut(e.target.value)} maxLength={24} placeholder="e.g. hours" /></label>
          </div>
          <label>Text<textarea value={body} onChange={(e) => setBody(e.target.value)} maxLength={1600} required /></label>
          {err && <p className="muted" style={{ color: "var(--bad-fg)" }}>{err}</p>}
          <div className="row">
            <button type="submit" className="primary">{editing ? "Save" : "Add"}</button>
            {editing && <button type="button" onClick={() => start(null)}>Cancel</button>}
          </div>
        </form>
      )}
    </div>
  );
}

function ChannelsPane({ tenant, widget: initialWidget, whatsapp: initialWa }: { tenant: string; widget: ChatWidgetInfo | null; whatsapp: WhatsAppInfo | null }) {
  const [w, setW] = useState<ChatWidgetInfo | null>(initialWidget);
  const [title, setTitle] = useState(initialWidget?.title ?? "Chat with us");
  const [greeting, setGreeting] = useState(initialWidget?.greeting ?? "");
  const [colour, setColour] = useState(initialWidget?.colour ?? "#3b5bdb");
  const [origins, setOrigins] = useState((initialWidget?.allowed_origins ?? []).join(", "));
  const [copied, setCopied] = useState(false);

  const [wa, setWa] = useState<WhatsAppInfo | null>(initialWa);
  const [phoneId, setPhoneId] = useState(initialWa?.phone_number_id ?? "");
  const [display, setDisplay] = useState(initialWa?.display_number ?? "");
  const [token, setToken] = useState("");
  const [waErr, setWaErr] = useState<string | null>(null);

  const saveWidget = async (extra: { enabled?: boolean; voice_enabled?: boolean; rotate_token?: boolean } = {}) => {
    const r = await patchWidget(tenant, {
      title: title.trim() || undefined,
      greeting: greeting.trim() || undefined,
      colour,
      allowed_origins: origins.split(",").map((s) => s.trim()).filter(Boolean),
      ...extra,
    });
    if (r) setW(r);
  };
  const copy = async () => {
    if (!w) return;
    await navigator.clipboard.writeText(w.snippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  const saveWa = async () => {
    setWaErr(null);
    const r = await saveWhatsApp(tenant, { phone_number_id: phoneId.trim(), display_number: display.trim() || undefined, access_token: token.trim() || undefined });
    if (!r.ok) { setWaErr(r.error ?? "save failed"); return; }
    setWa(r.data);
    setToken("");
  };

  return (
    <div className="two-col">
      <form className="section form" onSubmit={(e) => { e.preventDefault(); void saveWidget(); }}>
        <div className="row between">
          <h2>Web chat widget</h2>
          {w && <span className={`pill ${w.enabled ? "ok" : ""}`}>{w.enabled ? "enabled" : "disabled"}</span>}
        </div>
        <p className="hint">A chat bubble for your website using the same assistant, FAQs, hours and booking as the phone line. Conversations appear in this inbox.</p>
        <div className="two">
          <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={60} /></label>
          <label>Accent colour<input type="color" value={colour} onChange={(e) => setColour(e.target.value)} /></label>
        </div>
        <label>Greeting<textarea value={greeting} onChange={(e) => setGreeting(e.target.value)} maxLength={300} /></label>
        <label>Allowed website origins <span className="muted">(comma-separated, blank = any)</span><input value={origins} onChange={(e) => setOrigins(e.target.value)} placeholder="https://www.example.co.uk" /></label>
        <div className="row">
          <button type="submit" className="primary">Save</button>
          {w && <button type="button" onClick={() => void saveWidget({ enabled: !w.enabled })}>{w.enabled ? "Disable" : "Enable"}</button>}
          {w && <button type="button" onClick={() => void saveWidget({ voice_enabled: !w.voice_enabled })}>{w.voice_enabled ? "Turn off click-to-talk" : "Turn on click-to-talk"}</button>}
          {w && <button type="button" onClick={() => { if (confirm("Rotate the embed token? Existing embeds stop working.")) void saveWidget({ rotate_token: true }); }}>Rotate token</button>}
        </div>
        {w && (
          <>
            <label>Embed snippet <span className="muted">(paste before <code>&lt;/body&gt;</code>)</span>
              <textarea readOnly value={w.snippet} rows={4} onFocus={(e) => e.currentTarget.select()} />
            </label>
            <div className="row">
              <button type="button" onClick={() => void copy()}>{copied ? "Copied" : "Copy snippet"}</button>
              <a className="btn" href={w.embed_url} target="_blank" rel="noreferrer">Open chat page</a>
            </div>
          </>
        )}
      </form>

      <form className="section form" onSubmit={(e) => { e.preventDefault(); void saveWa(); }}>
        <div className="row between">
          <h2>WhatsApp Business</h2>
          {wa && <span className={`pill ${wa.has_token ? "ok" : "warn"}`}>{wa.has_token ? "connected" : "token missing"}</span>}
        </div>
        <p className="hint">Connect a Meta WhatsApp Cloud API number. Inbound messages arrive here and the assistant replies on WhatsApp.</p>
        <div className="two">
          <label>Phone number ID<input value={phoneId} onChange={(e) => setPhoneId(e.target.value)} placeholder="From Meta → WhatsApp → API setup" required /></label>
          <label>Display number<input value={display} onChange={(e) => setDisplay(e.target.value)} placeholder="+44 20 …" /></label>
        </div>
        <label>Permanent access token{wa?.has_token && <span className="muted"> (leave blank to keep current)</span>}<input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" /></label>
        {waErr && <p className="muted" style={{ color: "var(--bad-fg)" }}>{waErr}</p>}
        <button type="submit" className="primary">Save</button>
        {wa && (
          <div className="muted small">
            <p>In Meta&apos;s app dashboard → WhatsApp → Configuration, set:</p>
            <p>Callback URL: <code>{wa.webhook_url}</code></p>
            <p>Verify token: <code>{wa.verify_token}</code></p>
            <p>Subscribe to the <code>messages</code> field.</p>
          </div>
        )}
      </form>
    </div>
  );
}
