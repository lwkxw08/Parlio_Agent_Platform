"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { request, post, type OutboundCall, type Ticket, type TicketEvent } from "@/lib/api";

export default function TicketActions({ ticket: t, openAi = false }: { ticket: Ticket; openAi?: boolean }) {
  const router = useRouter();
  const [actor, setActor] = useState("me");
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [aiOpen, setAiOpen] = useState(openAi);
  const [kind, setKind] = useState<"answer" | "transfer" | "booking">("answer");
  const [resolution, setResolution] = useState("");
  const [transferTo, setTransferTo] = useState("");

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
  const aiReady = kind === "booking" || (kind === "answer" ? resolution.trim().length > 0 : transferTo.trim().length > 0);
  const aiCallback = () =>
    run(async () => {
      const r = await request<OutboundCall>(`/v1/outbound/calls?tenant_id=${t.tenant_id}`, {
        method: "POST",
        body: JSON.stringify({
          purpose: "ticket_callback", to: t.caller_number, name: t.caller_name, ticket_id: t.id,
          context: { callback_window: t.callback_window ?? "" },
          resolution_kind: kind,
          resolution: kind === "answer" ? resolution.trim() : undefined,
          transfer_to: kind === "transfer" ? transferTo.trim() : undefined,
        }),
      });
      if (r.ok) {
        setAiOpen(false);
        setResolution("");
        flash(`AI will call ${t.caller_number} at ${new Date(r.data.scheduled_at).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" })}`);
      } else {
        flash(`Could not schedule: ${r.error || "outbound disabled, outside calling hours or number on do-not-call list"}`);
      }
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
          <button disabled={busy} className={aiOpen ? "" : "ghost"} onClick={() => setAiOpen((v) => !v)} title="The assistant rings them back with the answer, a person ready to talk, or a booking to make (calling hours and do-not-call list apply)">Send to AI call back</button>
        )}
        {open && <button disabled={busy} onClick={() => act("resolve")}>Resolve</button>}
        {!open && <button disabled={busy} onClick={() => act("reopen")}>Reopen</button>}
      </div>
      {aiOpen && open && t.caller_number && (
        <div className="section" style={{ marginTop: "0.8rem", marginBottom: 0 }}>
          <h2>What will the assistant give {t.caller_name?.split(" ")[0] ?? "them"}?</h2>
          <p className="hint">An AI call back only goes out with a resolution in hand — it never rings to say nobody is free, and it never opens a second ticket.</p>
          <div className="chips" style={{ marginBottom: "0.8rem" }}>
            <button type="button" className={kind === "answer" ? "active" : ""} onClick={() => setKind("answer")}>Relay an answer</button>
            <button type="button" className={kind === "transfer" ? "active" : ""} onClick={() => setKind("transfer")}>Put them through to someone</button>
            <button type="button" className={kind === "booking" ? "active" : ""} onClick={() => setKind("booking")}>Book an appointment</button>
          </div>
          {kind === "answer" && (
            <label className="field">
              <span>The update to give them</span>
              <textarea rows={3} value={resolution} onChange={(e) => setResolution(e.target.value)} placeholder="e.g. The invoice has been corrected to £340 including VAT and a new copy has been emailed — nothing more to pay until it arrives." />
            </label>
          )}
          {kind === "transfer" && (
            <label className="field">
              <span>Who will take the call (must be free when the assistant rings)</span>
              <input value={transferTo} onChange={(e) => setTransferTo(e.target.value)} placeholder="e.g. Dave in Accounts" />
            </label>
          )}
          {kind === "booking" && <p className="muted small">The assistant will check the calendar with them and book the slot they asked for.</p>}
          <div className="row actions" style={{ marginTop: "0.6rem" }}>
            <button disabled={busy || !aiReady} onClick={aiCallback}>Queue the call back</button>
            <button className="ghost" type="button" onClick={() => setAiOpen(false)}>Cancel</button>
          </div>
        </div>
      )}
      <div className="row">
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note (optional, saved with the action)" style={{ flex: 1 }} />
        <button disabled={busy || !note.trim()} onClick={addNote}>Add note</button>
      </div>
      {notice && <p className="muted small">{notice}</p>}
    </div>
  );
}
