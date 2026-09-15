"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { fetchSnapshot, type Snapshot } from "@/lib/api";

const POLL_MS = 5000;

const seconds = (s: number | null) => (s == null ? "—" : s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`);

function Stat({
  label, value, hint, href, tone,
}: { label: string; value: string | number; hint: string; href?: string; tone?: "ok" | "warn" | "bad" }) {
  const body = (
    <>
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      <div className="hint">{hint}</div>
    </>
  );
  return href ? <Link href={href} className="card stat">{body}</Link> : <div className="card stat">{body}</div>;
}

export default function LiveSnapshot({ tenantId, initial }: { tenantId: string; initial: Snapshot | null }) {
  const [s, setS] = useState<Snapshot | null>(initial);

  const refresh = useCallback(async () => {
    const r = await fetchSnapshot(tenantId);
    if (r) setS(r);
  }, [tenantId]);

  useEffect(() => {
    const t = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  if (!s) return <p className="muted">API unreachable</p>;
  const tickets = s.open_tickets + s.claimed_tickets;
  return (
    <>
      <div className="row between">
        <h2 style={{ margin: 0 }}>Right now</h2>
        <span className="small muted">Live · updated {new Date(s.generated_at).toLocaleTimeString("en-GB")}</span>
      </div>
      <div className="grid">
        <Stat label="Active calls" value={s.active_calls} href="/live" tone={s.active_calls ? "ok" : undefined}
          hint={s.transferring ? `${s.transferring} being transferred to your team` : "Calls the assistant is handling now"} />
        <Stat label="Chats waiting" value={s.waiting_chats} href="/inbox" tone={s.waiting_chats ? "warn" : undefined}
          hint={s.unread_messages ? `${s.unread_messages} unread messages in Inbox` : "Web chats / messages waiting for a person"} />
        <Stat label="Open tickets" value={tickets} href="/tickets" tone={s.sla_breached ? "bad" : undefined}
          hint={s.sla_breached ? `${s.sla_breached} past their response target` : `${s.open_tickets} unclaimed · ${s.claimed_tickets} being worked`} />
        <Stat label="Callbacks due" value={s.callbacks_due} href="/tickets" hint="Callers waiting for you to ring them back" />
        {s.approvals_pending > 0 && (
          <Stat label="Needs your approval" value={s.approvals_pending} href="/live" tone="warn" hint="The assistant is waiting on a decision" />
        )}
      </div>

      <h2>Today</h2>
      <div className="grid">
        <Stat label="Calls today" value={s.calls_today} href="/calls" hint={`${s.answered_today} answered by the assistant`} />
        <Stat label="Missed today" value={s.missed_today} href="/calls?kind=missed" tone={s.missed_today ? "bad" : undefined}
          hint="Calls that ended before anyone spoke" />
        <Stat label="Transferred today" value={s.transferred_today} href="/handoff" hint="Connected through to a person" />
        <Stat label="Messages taken" value={s.tickets_today} href="/tickets" hint="Calls that ended with a callback ticket" />
        <Stat label="Time to answer" value={seconds(s.avg_answer_s)} hint="From ringing to the assistant speaking" />
        <Stat label="Assistant response time" value={seconds(s.avg_response_s)} hint="From the caller finishing to the reply starting" />
      </div>
    </>
  );
}
