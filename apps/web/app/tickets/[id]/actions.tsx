"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { post, type OutboundCall, type Ticket, type TicketEvent } from "@/lib/api";

export default function TicketActions({ ticket: t }: { ticket: Ticket }) {
  const router = useRouter();
  const [actor, setActor] = useState("me");
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const flash = (msg: string) => {
    setNotice(msg);
    setTimeout(() => setNotice(null), 6000);
  };
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
      router.refresh();
    } finally {
      setBusy(false);
    }
  };
  const act = (action: "claim" | "resolve" | "reopen" | "callback") =>
    run(async () => {
      const res = await post<Ticket | { tel_uri: string }>(`/v1/tickets/${t.id}/${action}`, { actor, note: note || undefined });
      if (res && "tel_uri" in res) window.location.href = res.tel_uri;
      setNote("");
    });
  const addNote = () =>
    run(async () => {
      await post<TicketEvent[]>(`/v1/tickets/${t.id}/notes`, { actor, note });
      setNote("");
    });
  const aiCallback = () =>
    run(async () => {
      const r = await post<OutboundCall>(`/v1/outbound/calls?tenant_id=${t.tenant_id}`, {
        purpose: "ticket_callback", to: t.caller_number, name: t.caller_name, ticket_id: t.id,
        context: { reason: t.reason, callback_window: t.callback_window ?? "" },
      });
      flash(r ? `AI will call ${t.caller_number} at ${new Date(r.scheduled_at).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" })}` : "Could not schedule (outbound disabled, outside calling hours or number on do-not-call list)");
    });

  const open = t.status !== "resolved" && t.status !== "cancelled";
  return (
    <div className="card">
      <p className="muted">
        Acting as <input value={actor} onChange={(e) => setActor(e.target.value)} className="inline" />
      </p>
      <div className="row actions">
        {t.status === "open" && <button disabled={busy} onClick={() => act("claim")}>Claim</button>}
        {open && t.caller_number && (
          <button disabled={busy} onClick={() => act("callback")} title="Ring the caller yourself now (claims the ticket to you)">Call back myself</button>
        )}
        {open && t.caller_number && (
          <button disabled={busy} onClick={aiCallback} title="Queue the assistant to ring them back and progress the ticket (calling hours and do-not-call list apply)">Send to AI call back</button>
        )}
        {open && <button disabled={busy} onClick={() => act("resolve")}>Resolve</button>}
        {!open && <button disabled={busy} onClick={() => act("reopen")}>Reopen</button>}
      </div>
      <div className="row">
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note (optional, saved with the action)" style={{ flex: 1 }} />
        <button disabled={busy || !note.trim()} onClick={addNote}>Add note</button>
      </div>
      {notice && <p className="muted small">{notice}</p>}
    </div>
  );
}
