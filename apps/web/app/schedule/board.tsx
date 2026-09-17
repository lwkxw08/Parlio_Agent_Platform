"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  type Booking,
  type Resource,
  type ScheduleBlock,
  type ScheduleLane,
  type ScheduleView,
  type ServiceType,
  type Site,
  cancelBooking,
  fetchBooking,
  fetchSchedule,
  phone,
  reassignBooking,
  rescheduleBooking,
} from "@/lib/api";
import { humanize } from "@/app/breakdown";

type Props = {
  tenant: string;
  initial: ScheduleView | null;
  error: string | null;
  start: string;
  days: number;
  resources: Resource[];
  services: ServiceType[];
  sites: Site[];
  canManage: boolean;
};

const DAY_MS = 86_400_000;
const addDays = (ymd: string, n: number) => new Date(new Date(`${ymd}T00:00:00Z`).getTime() + n * DAY_MS).toISOString().slice(0, 10);
const parts = (iso: string, tz: string) => {
  const p = new Intl.DateTimeFormat("en-GB", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).formatToParts(new Date(iso));
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? "00";
  return { ymd: `${g("year")}-${g("month")}-${g("day")}`, minutes: Number(g("hour")) * 60 + Number(g("minute")) };
};
const hm = (mins: number) => `${String(Math.floor(mins / 60)).padStart(2, "0")}:${String(mins % 60).padStart(2, "0")}`;
const fmtTime = (iso: string, tz: string) => new Date(iso).toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" });
const fmtDay = (ymd: string, long = false) => new Date(`${ymd}T12:00:00Z`).toLocaleDateString("en-GB", long ? { weekday: "long", day: "numeric", month: "long", year: "numeric" } : { weekday: "short", day: "numeric", month: "short" });
const STATUS_CLASS: Record<string, string> = { confirmed: "ok", cancelled: "bad", reschedule_requested: "warn", booked: "" };

export default function ScheduleBoard({ tenant, initial, error: initialError, start, days, resources, services, sites, canManage }: Props) {
  const router = useRouter();
  const [view, setView] = useState<ScheduleView | null>(initial);
  const [error, setError] = useState<string | null>(initialError);
  const [busy, setBusy] = useState(false);
  const [siteId, setSiteId] = useState("");
  const [serviceId, setServiceId] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [selected, setSelected] = useState<ScheduleBlock | null>(null);
  const tz = view?.timezone ?? "Europe/London";

  const load = async (refresh = false) => {
    setBusy(true);
    const r = await fetchSchedule(tenant, { start, days, site_id: siteId || undefined, service_id: serviceId || undefined, resource_id: resourceId || undefined, refresh });
    setBusy(false);
    if (r.ok) { setView(r.data); setError(null); } else setError(r.error);
  };
  // Filters re-query the read model; date/range changes go through the URL so they're shareable.
  useEffect(() => { void load(); }, [siteId, serviceId, resourceId]); // eslint-disable-line react-hooks/exhaustive-deps

  const nav = (s: string, d = days) => router.push(`/schedule?tenant=${tenant}&start=${s}&days=${d}`);
  const dayList = useMemo(() => Array.from({ length: days }, (_, i) => addDays(start, i)), [start, days]);
  const readOnly = view?.read_only ?? false;
  const actions = canManage && !readOnly;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
        <h1 style={{ margin: 0 }}>Schedule</h1>
        <div className="small muted">
          {view?.source === "scheduler" ? "From your scheduling tool" : "From connected calendars"}
          {readOnly && <span className="pill warn" style={{ marginLeft: 6 }}>Read-only</span>}
          {view?.cached && <span title="Cached for 30 s"> · cached</span>}
          {view && <> · {fmtTime(view.generated_at, tz)}</>}
        </div>
      </div>
      <div className="toolbar" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", margin: "0.8rem 0" }}>
        <button className="ghost" onClick={() => nav(addDays(start, -days))} aria-label="Previous">‹</button>
        <input type="date" value={start} onChange={(e) => e.target.value && nav(e.target.value)} />
        <button className="ghost" onClick={() => nav(addDays(start, days))} aria-label="Next">›</button>
        <button className="ghost" onClick={() => nav(new Date().toISOString().slice(0, 10))}>Today</button>
        <div className="tabs" style={{ border: 0, margin: 0 }}>
          <button className={days === 1 ? "active" : ""} onClick={() => nav(start, 1)}>Day</button>
          <button className={days === 7 ? "active" : ""} onClick={() => nav(start, 7)}>Week</button>
        </div>
        {sites.length > 0 && (
          <select value={siteId} onChange={(e) => setSiteId(e.target.value)}><option value="">All locations</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        )}
        {services.length > 0 && (
          <select value={serviceId} onChange={(e) => setServiceId(e.target.value)}><option value="">All services</option>{services.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        )}
        {resources.length > 1 && (
          <select value={resourceId} onChange={(e) => setResourceId(e.target.value)}><option value="">Everyone</option>{resources.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
        )}
        <button className="ghost" onClick={() => load(true)} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</button>
        <Link className="small" href={`/team?tenant=${tenant}&tab=engineers`}>Manage engineers</Link>
      </div>
      {error && <p className="hint warn">{error}</p>}
      {view && view.lanes.length === 0 && <p className="muted">Nothing to show — connect a calendar or add engineers under Team.</p>}
      {view && days === 1 && <DayView view={view} day={start} onPick={setSelected} />}
      {view && days === 7 && <WeekView view={view} dayList={dayList} onPick={setSelected} />}
      {selected && (
        <BlockDetail
          tenant={tenant}
          block={selected}
          tz={tz}
          resources={resources.filter((r) => r.active)}
          actions={actions}
          onClose={() => setSelected(null)}
          onChanged={() => { setSelected(null); void load(true); }}
        />
      )}
      <p className="hint" style={{ marginTop: "1rem" }}>
        Shaded areas are shifts; hatched blocks are other events already in the engineer&apos;s calendar; thin grey blocks are travel gaps.
        Click a booking to see the details{actions ? " and reassign, move or cancel it" : ""}.
      </p>
    </>
  );
}

function laneWindow(lanes: ScheduleLane[], day: string, tz: string): [number, number] {
  let lo = 8 * 60, hi = 18 * 60;
  for (const l of lanes) {
    for (const s of l.shifts) { const a = parts(s.start, tz), b = parts(s.end, tz); if (a.ymd === day) lo = Math.min(lo, a.minutes); if (b.ymd === day) hi = Math.max(hi, b.minutes); }
    for (const b of l.blocks) { const a = parts(b.start, tz), e = parts(b.end, tz); if (a.ymd === day) lo = Math.min(lo, a.minutes); if (e.ymd === day) hi = Math.max(hi, e.minutes); }
  }
  lo = Math.max(0, Math.floor(lo / 60) * 60 - 60);
  hi = Math.min(24 * 60, Math.ceil(hi / 60) * 60 + 60);
  return [lo, hi];
}

function DayView({ view, day, onPick }: { view: ScheduleView; day: string; onPick: (b: ScheduleBlock) => void }) {
  const tz = view.timezone;
  const [lo, hi] = laneWindow(view.lanes, day, tz);
  const span = hi - lo;
  const pct = (m: number) => `${((Math.min(hi, Math.max(lo, m)) - lo) / span) * 100}%`;
  const hours = Array.from({ length: Math.floor(span / 60) + 1 }, (_, i) => lo + i * 60).filter((h) => h <= hi);
  const inDay = (iso: string) => parts(iso, tz).ymd === day;
  const range = (s: string, e: string): [number, number] => {
    const a = parts(s, tz), b = parts(e, tz);
    return [a.ymd < day ? 0 : a.minutes, b.ymd > day ? 24 * 60 : b.minutes];
  };
  return (
    <div className="schedule-day" style={{ overflowX: "auto" }}>
      <div style={{ minWidth: 640 }}>
        <div style={{ display: "grid", gridTemplateColumns: "160px 1fr" }}>
          <div />
          <div style={{ position: "relative", height: 18 }}>
            {hours.map((h) => <span key={h} className="small muted" style={{ position: "absolute", left: pct(h), transform: "translateX(-50%)" }}>{hm(h)}</span>)}
          </div>
        </div>
        {view.lanes.map((l) => (
          <div key={l.resource_id ?? "single"} style={{ display: "grid", gridTemplateColumns: "160px 1fr", borderTop: "1px solid var(--line)", minHeight: 64 }}>
            <div style={{ padding: "0.5rem 0.4rem 0.5rem 0" }}>
              <div><strong>{l.name}</strong>{l.on_call && <span className="pill warn" style={{ marginLeft: 6 }}>On call</span>}</div>
              <div className="small muted">{l.role}{l.shift_minutes > 0 && <> · {Math.round(l.utilisation * 100)}% booked</>}</div>
              {l.error && <div className="small warn" title={l.error}>Calendar error</div>}
            </div>
            <div style={{ position: "relative", background: "var(--panel-2, rgba(127,127,127,0.08))", margin: "6px 0" }}>
              {hours.map((h) => <div key={h} style={{ position: "absolute", left: pct(h), top: 0, bottom: 0, borderLeft: "1px dashed var(--line)" }} />)}
              {l.shifts.filter((s) => inDay(s.start) || inDay(s.end)).map((s, i) => {
                const [a, b] = range(s.start, s.end);
                return <div key={i} style={{ position: "absolute", left: pct(a), width: `calc(${pct(b)} - ${pct(a)})`, top: 0, bottom: 0, background: "var(--bg)", opacity: 0.9 }} />;
              })}
              {l.blocks.filter((b) => inDay(b.start) || inDay(b.end)).map((b) => {
                const [a, e] = range(b.start, b.end);
                return <BlockChip key={b.id} b={b} left={pct(a)} width={`calc(${pct(e)} - ${pct(a)})`} tz={tz} onPick={onPick} />;
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function BlockChip({ b, left, width, tz, onPick }: { b: ScheduleBlock; left: string; width: string; tz: string; onPick: (b: ScheduleBlock) => void }) {
  const base: React.CSSProperties = { position: "absolute", left, width, top: 6, bottom: 6, borderRadius: 6, overflow: "hidden", fontSize: "0.75rem", padding: "2px 6px", boxSizing: "border-box" };
  if (b.kind === "travel") return <div title="Travel gap" style={{ ...base, top: 22, bottom: 22, background: "var(--line)" }} />;
  if (b.kind === "busy") {
    return (
      <div title={`${b.title || "Busy"} ${fmtTime(b.start, tz)}–${fmtTime(b.end, tz)}`} style={{ ...base, background: "repeating-linear-gradient(135deg, transparent 0 4px, var(--line) 4px 6px)", border: "1px solid var(--line)", color: "var(--muted)" }}>
        {b.title || "Busy"}
      </div>
    );
  }
  const cls = STATUS_CLASS[b.status ?? ""] ?? "";
  return (
    <button
      className={`block ${cls}`}
      onClick={() => onPick(b)}
      title={`${b.customer ?? ""} · ${b.service ?? ""} · ${fmtTime(b.start, tz)}–${fmtTime(b.end, tz)}`}
      style={{ ...base, textAlign: "left", cursor: "pointer", border: "1px solid var(--accent)", background: b.status === "cancelled" ? "transparent" : "var(--accent-soft, rgba(80,120,255,0.18))", color: "var(--fg)", textDecoration: b.status === "cancelled" ? "line-through" : undefined }}
    >
      <strong>{fmtTime(b.start, tz)}</strong> {b.customer}{b.service && <span className="muted"> · {b.service}</span>}{b.area && <span className="muted"> · {b.area}</span>}
    </button>
  );
}

function WeekView({ view, dayList, onPick }: { view: ScheduleView; dayList: string[]; onPick: (b: ScheduleBlock) => void }) {
  const tz = view.timezone;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="schedule-week" style={{ minWidth: 900 }}>
        <thead><tr><th style={{ width: 150 }}></th>{dayList.map((d) => <th key={d}>{fmtDay(d)}</th>)}</tr></thead>
        <tbody>
          {view.lanes.map((l) => (
            <tr key={l.resource_id ?? "single"}>
              <td style={{ verticalAlign: "top" }}>
                <strong>{l.name}</strong>{l.on_call && <span className="pill warn" style={{ marginLeft: 6 }}>On call</span>}
                <div className="small muted">{l.role}{l.shift_minutes > 0 && <> · {Math.round(l.utilisation * 100)}%</>}</div>
              </td>
              {dayList.map((d) => {
                const shift = l.shifts.find((s) => parts(s.start, tz).ymd === d);
                const blocks = l.blocks.filter((b) => b.kind !== "travel" && parts(b.start, tz).ymd === d);
                return (
                  <td key={d} style={{ verticalAlign: "top", background: shift ? undefined : "var(--panel-2, rgba(127,127,127,0.08))" }}>
                    {shift && <div className="small muted">{fmtTime(shift.start, tz)}–{fmtTime(shift.end, tz)}</div>}
                    {blocks.map((b) => b.kind === "busy" ? (
                      <div key={b.id} className="small muted" style={{ borderLeft: "3px solid var(--line)", paddingLeft: 4, margin: "2px 0" }}>{fmtTime(b.start, tz)} {b.title || "Busy"}</div>
                    ) : (
                      <button key={b.id} className={`ghost small ${STATUS_CLASS[b.status ?? ""] ?? ""}`} onClick={() => onPick(b)} style={{ display: "block", textAlign: "left", width: "100%", margin: "2px 0", padding: "2px 4px", borderLeft: "3px solid var(--accent)", textDecoration: b.status === "cancelled" ? "line-through" : undefined }}>
                        {fmtTime(b.start, tz)} {b.customer}{b.service && <span className="muted"> · {b.service}</span>}
                      </button>
                    ))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BlockDetail({ tenant, block, tz, resources, actions, onClose, onChanged }: { tenant: string; block: ScheduleBlock; tz: string; resources: Resource[]; actions: boolean; onClose: () => void; onChanged: () => void }) {
  const [booking, setBooking] = useState<Booking | null>(null);
  const [target, setTarget] = useState("");
  const [when, setWhen] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (!block.booking_id) return;
    void fetchBooking(tenant, block.booking_id).then((r) => { if (r.ok) setBooking(r.data); else setMsg(r.error); });
  }, [tenant, block.booking_id]);

  const run = async (fn: () => Promise<{ ok: true; data: Booking } | { ok: false; error: string }>) => {
    setWorking(true); setMsg(null);
    const r = await fn();
    setWorking(false);
    if (r.ok) onChanged(); else setMsg(r.error);
  };
  const id = block.booking_id ?? "";
  const cancelled = (booking?.status ?? block.status) === "cancelled";

  return (
    <div className="section" role="dialog" aria-label="Booking details" style={{ marginTop: "1rem", borderColor: "var(--accent)" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{block.customer ?? block.title}</h2>
        <button className="ghost" onClick={onClose}>Close</button>
      </div>
      <dl className="kv small" style={{ marginTop: "0.6rem" }}>
        <dt>When</dt><dd>{fmtDay(parts(block.start, tz).ymd, true)}, {fmtTime(block.start, tz)}–{fmtTime(block.end, tz)}</dd>
        <dt>Service</dt><dd>{block.service ?? booking?.service_name ?? "—"}</dd>
        <dt>Status</dt><dd><span className={`pill ${STATUS_CLASS[booking?.status ?? block.status ?? ""] ?? ""}`}>{humanize(booking?.status ?? block.status ?? "booked")}</span></dd>
        <dt>Assigned to</dt><dd>{booking?.resource_name ?? block.assignee ?? "Unassigned"}</dd>
        {booking?.phone && <><dt>Phone</dt><dd>{phone(booking.phone)}</dd></>}
        {booking?.address && <><dt>Address</dt><dd>{booking.address}</dd></>}
        {booking?.notes && <><dt>Notes</dt><dd>{booking.notes}</dd></>}
        {booking?.call_id && <><dt>Call</dt><dd><Link href={`/calls/${booking.call_id}`}>Open recording &amp; transcript</Link></dd></>}
      </dl>
      {msg && <p className="hint warn">{msg}</p>}
      {actions && id && !cancelled && (
        <div className="form" style={{ marginTop: "0.6rem" }}>
          {resources.length > 0 && (
            <div className="row" style={{ gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <select value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">Reassign to…</option>
                {resources.filter((r) => r.id !== booking?.resource_id).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
              <button disabled={!target || working} onClick={() => run(() => reassignBooking(tenant, id, target))}>Reassign</button>
            </div>
          )}
          <div className="row" style={{ gap: 6, alignItems: "center", flexWrap: "wrap", marginTop: 6 }}>
            <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
            <button disabled={!when || working} onClick={() => run(() => rescheduleBooking(tenant, id, new Date(when).toISOString()))}>Move</button>
            <button className="danger" disabled={working} onClick={() => { if (confirm("Cancel this booking? The calendar event is removed.")) void run(() => cancelBooking(tenant, id)); }}>Cancel booking</button>
          </div>
        </div>
      )}
      {!actions && id && <p className="hint">{resources.length === 0 && !booking?.resource_id ? "" : "Changes are made in your scheduling tool; this view is read-only."}</p>}
    </div>
  );
}
