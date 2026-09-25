"use client";

import { useEffect, useState } from "react";
import { type Assistant, type AvailableNumber, type NumberRegion, type TenantNumber, fetchNumberRegions, request } from "@/lib/api";

/** Area-code search + one-click provisioning of a UK number, shared by Billing → Numbers and Telephony. */
export function NumberPicker({ tenant, assistants, onProvisioned, onError, compact }: {
  tenant: string;
  assistants: Assistant[];
  onProvisioned: (n: TenantNumber) => void;
  onError: (m: string) => void;
  compact?: boolean;
}) {
  const [found, setFound] = useState<AvailableNumber[] | null>(null);
  const [assistant, setAssistant] = useState(assistants[0]?.assistant_id ?? "");
  const [label, setLabel] = useState("");
  const [regions, setRegions] = useState<NumberRegion[]>([]);
  const [region, setRegion] = useState("161");
  const [searching, setSearching] = useState(false);
  const [buying, setBuying] = useState<string | null>(null);
  const q = `?tenant_id=${tenant}`;

  useEffect(() => { fetchNumberRegions().then((r) => r && setRegions(r)); }, []);

  const search = async () => {
    setSearching(true);
    const r = await request<AvailableNumber[]>(`/v1/numbers/search${q}&country=GB&limit=8&area_code=${region}`);
    setSearching(false);
    if (r.ok) setFound(r.data); else onError(`Search failed: ${r.error}`);
  };
  const pretty = (e164: string) => {
    const n = "0" + e164.slice(3);
    const code = regions.find((x) => n.startsWith("0" + x.code))?.code;
    if (!code) return n;
    const rest = n.slice(code.length + 1);
    const mid = rest.length >= 7 ? Math.ceil(rest.length / 2) : rest.length;
    return `0${code} ${rest.slice(0, mid)} ${rest.slice(mid)}`.trim();
  };
  const buy = async (e164: string) => {
    setBuying(e164);
    const r = await request<TenantNumber>(`/v1/numbers${q}`, { method: "POST", body: JSON.stringify({ e164, assistant_id: assistant, label: label || null }) });
    setBuying(null);
    if (!r.ok) return onError(`Could not provision: ${r.error}`);
    setFound(null);
    onProvisioned(r.data);
  };

  return (
    <>
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
        <select value={region} onChange={(e) => { setRegion(e.target.value); setFound(null); }}>
          <optgroup label="Local (geographic)">{regions.filter((r) => r.kind === "geographic").map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}</optgroup>
          <optgroup label="UK-wide">{regions.filter((r) => r.kind !== "geographic").map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}</optgroup>
        </select>
        {assistants.length > 1 && (
          <select value={assistant} onChange={(e) => setAssistant(e.target.value)}>
            {assistants.map((a) => <option key={a.assistant_id} value={a.assistant_id}>{a.name}</option>)}
          </select>
        )}
        {!compact && <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label (optional)" style={{ maxWidth: 200 }} />}
        <button className="primary" onClick={search} disabled={searching}>{searching ? "Searching…" : found ? "Search again" : "Show available numbers"}</button>
      </div>
      {found && (
        <>
          {found.length > 0 && <p className="small muted" style={{ marginTop: "0.8rem", marginBottom: 0 }}>Click a number to make it yours:</p>}
          <div className="chips" style={{ marginTop: "0.4rem" }}>
            {found.map((n) => <a key={n.e164} title={n.e164} onClick={() => buying || buy(n.e164)}>{buying === n.e164 ? "Ordering…" : pretty(n.e164)}</a>)}
            {found.length === 0 && <span className="muted small">No {regions.find((r) => r.code === region)?.label ?? ""} numbers available right now — try another area code.</span>}
          </div>
          {found[0]?.provider === "simulated" && <p className="small muted" style={{ marginTop: "0.4rem" }}>These are simulated numbers (no carrier connected on this environment) — they route in the dashboard but can&apos;t receive real calls.</p>}
        </>
      )}
    </>
  );
}

export const provisionedMessage = (n: TenantNumber, asstName: string) =>
  n.status === "pending"
    ? `${n.e164} is yours and routed to ${asstName}. The carrier is completing its regulatory check before it can take calls - usually minutes, occasionally a few hours. We'll email you the moment it's live.`
    : `${n.e164} is now live and routed to ${asstName}.`;
