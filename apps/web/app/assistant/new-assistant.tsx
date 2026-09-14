"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { type Assistant, createAssistant } from "@/lib/api";

export default function NewAssistant({ assistants }: { assistants: Assistant[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [business, setBusiness] = useState(assistants[0]?.business_name ?? "");
  const [copyFrom, setCopyFrom] = useState<string>(assistants[0]?.assistant_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <button type="button" className="secondary small" onClick={() => setOpen(true)}>
        + New assistant
      </button>
    );
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await createAssistant({ name, business_name: business, copy_from: copyFrom || null });
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setOpen(false);
    router.push(`/assistant?id=${res.data.assistant_id}`);
    router.refresh();
  };

  return (
    <form onSubmit={submit} className="card" style={{ display: "grid", gap: ".6rem", maxWidth: 520 }}>
      <strong>New assistant</strong>
      <p className="muted small" style={{ margin: 0 }}>
        Use separate assistants for different sites, brands or lines (e.g. sales vs after-hours). Each one has its own
        greeting, hours and routing; give it a phone number under Telephony once created.
      </p>
      <label>
        Assistant name
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Aria" required />
      </label>
      <label>
        Business name (as the assistant will say it)
        <input value={business} onChange={(e) => setBusiness(e.target.value)} required />
      </label>
      <label>
        Start from
        <select value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
          <option value="">Blank defaults</option>
          {assistants.map((a) => (
            <option key={a.assistant_id} value={a.assistant_id}>
              Copy settings from {a.name} ({a.business_name})
            </option>
          ))}
        </select>
      </label>
      {error && <p className="error small">{error}</p>}
      <div style={{ display: "flex", gap: ".5rem" }}>
        <button type="submit" disabled={busy}>{busy ? "Creating…" : "Create assistant"}</button>
        <button type="button" className="secondary" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </form>
  );
}
