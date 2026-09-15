"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type Approval,
  type JoinInfo,
  type LiveCall,
  type LiveMessage,
  type SupervisorCommand,
  commandLiveCall,
  decideApproval,
  joinLiveCall,
  leaveLiveCall,
  liveSocketUrl,
  when,
} from "@/lib/api";
import { useRoomAudio } from "./room";
import { humanize } from "@/app/breakdown";

type Props = {
  tenant: string;
  me: string;
  canControl: boolean;
  initialCalls: LiveCall[];
  initialApprovals: Approval[];
};

const STATUS_PILL: Record<string, string> = { ringing: "warn", in_progress: "ok", transferring: "warn", escalated: "bad" };

function elapsed(from: string, now: number): string {
  const s = Math.max(0, Math.floor((now - new Date(from).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function money(a: number | null, ccy: string): string {
  if (a === null) return "";
  return new Intl.NumberFormat("en-GB", { style: "currency", currency: ccy || "GBP" }).format(a);
}

export default function Live({ tenant, me, canControl, initialCalls, initialApprovals }: Props) {
  const [calls, setCalls] = useState<Map<string, LiveCall>>(() => new Map(initialCalls.map((c) => [c.call_id, c])));
  const [approvals, setApprovals] = useState<Approval[]>(initialApprovals);
  const [selected, setSelected] = useState<string | null>(initialCalls[0]?.call_id ?? null);
  const [socket, setSocket] = useState<"connecting" | "live" | "offline">("connecting");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const onMessage = useCallback((m: LiveMessage) => {
    if (m.type === "snapshot") {
      setCalls(new Map((m.calls ?? []).map((c) => [c.call_id, c])));
      return;
    }
    if (m.type === "approval.requested" || m.type === "approval.decided") {
      const ap = m.payload as unknown as Approval;
      setApprovals((prev) => [ap, ...prev.filter((a) => a.id !== ap.id)]);
      return;
    }
    if (m.type === "call.ended" || m.type === "call.failed") {
      setCalls((prev) => {
        const next = new Map(prev);
        if (m.call_id) next.delete(m.call_id);
        return next;
      });
      return;
    }
    if (m.call) {
      const call = m.call;
      setCalls((prev) => new Map(prev).set(call.call_id, call));
    }
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const connect = async () => {
      setSocket("connecting");
      ws = new WebSocket(await liveSocketUrl(tenant));
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
  }, [tenant, onMessage]);

  const list = useMemo(() => [...calls.values()].sort((a, b) => a.started_at.localeCompare(b.started_at)), [calls]);
  const current = selected ? calls.get(selected) ?? null : null;
  useEffect(() => {
    if (!current && list.length) setSelected(list[0].call_id);
  }, [current, list]);
  const pending = approvals.filter((a) => a.status === "pending");
  const decided = approvals.filter((a) => a.status !== "pending").slice(0, 20);

  return (
    <>
      <div className="grid">
        <div className="card"><div className="label">Active calls</div><div className="value">{list.length}</div></div>
        <div className="card"><div className="label">Awaiting approval</div><div className="value">{pending.length}</div></div>
        <div className="card"><div className="label">Being handled by a human</div><div className="value">{list.filter((c) => c.supervisor_mode === "taken_over").length}</div></div>
        <div className="card"><div className="label">Stream</div><div className="value"><span className={`pill ${socket === "live" ? "ok" : socket === "offline" ? "bad" : "warn"}`}>{humanize(socket)}</span></div></div>
      </div>

      <div className="live-layout">
        <div className="section live-list">
          <h2>In progress</h2>
          <p className="hint">Calls appear here the moment they ring and leave when they end.</p>
          {!list.length && <p className="muted">No calls in progress. Completed calls are under <Link href="/calls">Calls</Link>.</p>}
          {list.map((c) => (
            <button key={c.call_id} className={`live-row ${c.call_id === selected ? "active" : ""}`} onClick={() => setSelected(c.call_id)}>
              <span className="row between">
                <strong>{c.caller ?? "withheld"}</strong>
                <span className="muted small">{elapsed(c.started_at, now)}</span>
              </span>
              <span className="row small">
                <span className={`pill ${STATUS_PILL[c.escalated ? "escalated" : c.status] ?? ""}`}>{c.escalated ? "escalated" : c.status.replace("_", " ")}</span>
                <span className="pill">{humanize(c.direction)}</span>
                {c.supervisor_mode !== "none" && <span className="pill warn">{c.supervisor_mode === "taken_over" ? "human" : "listening"}</span>}
                {c.pending_approval_id && <span className="pill bad">approval</span>}
              </span>
              <span className="muted small live-last">{c.transcript.at(-1)?.text ?? "…"}</span>
            </button>
          ))}
        </div>

        <div className="live-detail">
          {current ? (
            <CallPanel key={current.call_id} tenant={tenant} me={me} call={current} canControl={canControl} approvals={approvals.filter((a) => a.call_id === current.call_id)} onApproval={(a) => setApprovals((p) => [a, ...p.filter((x) => x.id !== a.id)])} />
          ) : (
            <div className="section"><p className="muted">Select a call to watch its transcript, listen in, whisper to the assistant or take over.</p></div>
          )}
        </div>
      </div>

      <div className="section">
        <h2>Approvals</h2>
        <p className="hint">The assistant asks before committing to quotes, bookings, refunds or discounts. Approve here or via the SMS/Slack link.</p>
        {!pending.length && <p className="muted">Nothing waiting for a decision.</p>}
        {pending.map((a) => <ApprovalRow key={a.id} tenant={tenant} a={a} now={now} canDecide={canControl} onDone={(x) => setApprovals((p) => [x, ...p.filter((y) => y.id !== x.id)])} />)}
        {decided.length > 0 && (
          <table style={{ marginTop: "1rem" }}>
            <thead><tr><th>When</th><th>Type</th><th>Request</th><th>Amount</th><th>Outcome</th><th>By</th></tr></thead>
            <tbody>
              {decided.map((a) => (
                <tr key={a.id}>
                  <td className="muted small">{when(a.decided_at ?? a.requested_at)}</td>
                  <td><span className="pill">{humanize(a.kind)}</span></td>
                  <td>{a.title}</td>
                  <td>{money(a.amount, a.currency)}</td>
                  <td><span className={`pill ${a.status === "approved" ? "ok" : a.status === "rejected" ? "bad" : "warn"}`}>{humanize(a.status)}</span></td>
                  <td className="muted small">{a.decided_by ?? "—"}{a.note ? ` · ${a.note}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

// -- one call ---------------------------------------------------------------------------------

function CallPanel({ tenant, me, call, canControl, approvals, onApproval }: {
  tenant: string; me: string; call: LiveCall; canControl: boolean; approvals: Approval[]; onApproval: (a: Approval) => void;
}) {
  const [join, setJoin] = useState<JoinInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const room = useRoomAudio(join);
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [call.transcript.length]);

  const mine = call.supervisor === me || call.supervisor === null;
  const takenByOther = call.supervisor_mode === "taken_over" && !mine;

  const run = async (label: string, fn: () => Promise<{ ok: true; data: unknown } | { ok: false; error: string }>) => {
    setBusy(label); setErr(null);
    const r = await fn();
    if (!r.ok) setErr(r.error);
    setBusy(null);
    return r;
  };

  const listen = () => run("listen", async () => {
    const r = await joinLiveCall(tenant, call.call_id);
    if (r.ok) setJoin(r.data);
    return r;
  });
  const leave = () => run("leave", async () => {
    setJoin(null);
    return leaveLiveCall(tenant, call.call_id);
  });
  const send = (cmd: SupervisorCommand, t?: string) => run(cmd, async () => {
    const r = await commandLiveCall(tenant, call.call_id, cmd, t);
    if (r.ok && r.data) setJoin(r.data);
    if (r.ok && cmd === "handback" && join) setJoin({ ...join, mode: "listening" });
    if (r.ok && (cmd === "whisper" || cmd === "say")) setText("");
    return r;
  });

  return (
    <div className="section">
      <div className="row between">
        <div>
          <h2 style={{ margin: 0 }}>{call.caller ?? "Withheld number"} <span className="muted small">→ {call.dialed ?? "—"}</span></h2>
          <p className="muted small" style={{ margin: "0.2rem 0 0" }}>
            {call.direction} · started {when(call.started_at)} · {call.transfers} transfer{call.transfers === 1 ? "" : "s"} · {call.tickets} ticket{call.tickets === 1 ? "" : "s"}
            {call.supervisor && <> · {call.supervisor_mode === "taken_over" ? "handled by" : "watched by"} <strong>{call.supervisor === me ? "you" : call.supervisor}</strong></>}
          </p>
        </div>
        <div className="row">
          {!join && <button className="primary" disabled={busy !== null || !call.room} onClick={listen}>Listen</button>}
          {join && <button className="ghost" disabled={busy !== null} onClick={leave}>Stop listening</button>}
          {canControl && join?.mode !== "taken_over" && (
            <button className="primary" disabled={busy !== null || takenByOther} onClick={() => send("takeover")} title="Mute the assistant and speak to the caller yourself">Take over</button>
          )}
          {canControl && join?.mode === "taken_over" && (
            <button className="ghost" disabled={busy !== null} onClick={() => send("handback")}>Hand back to AI</button>
          )}
          {canControl && <button className="danger" disabled={busy !== null || takenByOther} onClick={() => { if (confirm("End this call?")) void send("hangup"); }}>Hang up</button>}
        </div>
      </div>
      {err && <p className="muted small" style={{ color: "var(--bad-fg)" }}>{err}</p>}
      {join && (
        <p className="small muted" style={{ margin: "0.5rem 0 0" }}>
          Audio: <span className={`pill ${room.state === "connected" ? "ok" : room.state === "error" ? "bad" : "warn"}`}>{humanize(room.state)}</span>
          {room.state === "connected" && join.mode === "taken_over" && <> · your microphone is live — the assistant is muted</>}
          {room.state === "connected" && join.mode === "listening" && <> · listening only</>}
          {room.error && <> · {room.error}</>}
        </p>
      )}

      <div className="transcript live-transcript" ref={logRef}>
        {call.transcript.map((t, i) => (
          <div key={i} className={`msg ${t.role === "user" ? "user" : t.role === "supervisor" ? "supervisor" : "assistant"}`}>
            <span className="who muted small">{t.role === "user" ? "Caller" : t.role === "supervisor" ? "Supervisor" : "Assistant"}</span>
            <div>{t.text}</div>
          </div>
        ))}
        {!call.transcript.length && <p className="muted">Waiting for the first words…</p>}
      </div>

      {canControl && (
        <form className="form" style={{ marginTop: "0.8rem" }} onSubmit={(e) => { e.preventDefault(); if (text.trim()) void send("whisper", text.trim()); }}>
          <label>
            Coach the assistant
            <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Offer them the Thursday 10am slot, and mention the 10% first-visit discount" rows={2} disabled={takenByOther} />
          </label>
          <div className="row">
            <button type="submit" className="primary" disabled={busy !== null || !text.trim() || takenByOther}>Whisper to AI</button>
            <button type="button" className="ghost" disabled={busy !== null || !text.trim() || takenByOther} onClick={() => send("say", text.trim())} title="The assistant reads this to the caller word for word">Say to caller</button>
            <span className="muted small">Whispers are private guidance; “Say” is spoken verbatim.</span>
          </div>
        </form>
      )}

      {approvals.filter((a) => a.status === "pending").map((a) => (
        <ApprovalRow key={a.id} tenant={tenant} a={a} now={Date.now()} canDecide={canControl} onDone={onApproval} />
      ))}
    </div>
  );
}

// -- approvals --------------------------------------------------------------------------------

function ApprovalRow({ tenant, a, now, canDecide, onDone }: { tenant: string; a: Approval; now: number; canDecide: boolean; onDone: (a: Approval) => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const left = Math.max(0, Math.floor((new Date(a.expires_at).getTime() - now) / 1000));
  const decide = async (approve: boolean) => {
    setBusy(true); setErr(null);
    const r = await decideApproval(tenant, a.id, approve, note || undefined);
    if (r.ok) onDone(r.data); else setErr(r.error);
    setBusy(false);
  };
  return (
    <div className="approval">
      <div className="row between">
        <div>
          <span className="pill warn" style={{ marginRight: "0.4rem" }}>{humanize(a.kind)}</span>
          <strong>{a.title}</strong> {a.amount !== null && <span className="muted">· {money(a.amount, a.currency)}</span>}
          {a.details && <p className="muted small" style={{ margin: "0.3rem 0 0" }}>{a.details}</p>}
          <p className="muted small" style={{ margin: "0.3rem 0 0" }}>
            {a.caller ? `Caller ${a.caller} · ` : ""}asked {when(a.requested_at)} · {left > 0 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left` : "expiring"}
          </p>
        </div>
        {canDecide && (
          <div className="row">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="note (optional)" className="approval-note" />
            <button className="primary" disabled={busy || left === 0} onClick={() => decide(true)}>Approve</button>
            <button className="danger" disabled={busy || left === 0} onClick={() => decide(false)}>Reject</button>
          </div>
        )}
      </div>
      {err && <p className="muted small" style={{ color: "var(--bad-fg)" }}>{err}</p>}
    </div>
  );
}
