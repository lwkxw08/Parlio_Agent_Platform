"use client";

import { useState } from "react";
import { type NumberPoolSummary, type NumberRegion, buyPoolNumbers, releasePoolNumber, when } from "@/lib/api";

const prettyUk = (e164: string) => {
  if (!e164.startsWith("+44")) return e164;
  const n = "0" + e164.slice(3);
  if (n.startsWith("02")) return `${n.slice(0, 3)} ${n.slice(3, 7)} ${n.slice(7)}`;
  return `${n.slice(0, 5)} ${n.slice(5)}`;
};

type Props = { pool: NumberPoolSummary; regions: NumberRegion[]; canBuy: boolean };

/** Platform-owned stock of live numbers handed to new tenants instantly, ahead of carrier review. */
export default function NumberPoolCard({ pool: initial, regions, canBuy }: Props) {
  const [pool, setPool] = useState(initial);
  const [qty, setQty] = useState(3);
  const [area, setArea] = useState(regions[0]?.code ?? "20");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 8000); };

  const buy = async () => {
    if (busy) return;
    const label = regions.find((r) => r.code === area)?.label ?? `0${area}`;
    if (!confirm(`Buy ${qty} ${label} number${qty === 1 ? "" : "s"} now? Your carrier account is charged about £1/month per number until it's released.`)) return;
    setBusy(true);
    const r = await buyPoolNumbers(qty, area);
    setBusy(false);
    if (!r.ok) return flash(r.error);
    const pending = r.data.filter((n) => n.status === "pending").length;
    setPool((p) => {
      const numbers = [...p.numbers, ...r.data].sort((a, b) => a.e164.localeCompare(b.e164));
      return {
        numbers,
        available: numbers.filter((n) => n.status === "active").length,
        pending: numbers.filter((n) => n.status === "pending").length,
        failed: numbers.filter((n) => n.status === "failed").length,
        monthly_pence: numbers.reduce((s, n) => s + n.monthly_pence, 0),
      };
    });
    flash(r.data.length < qty
      ? `Only ${r.data.length} of ${qty} were available in that area.`
      : pending
        ? `${r.data.length} bought; ${pending} awaiting carrier review before they can be handed out.`
        : `${r.data.length} bought and live.`);
  };

  const release = async (id: string, e164: string) => {
    if (!confirm(`Release ${e164} back to the carrier? It stops being billed and leaves the pool.`)) return;
    if (await releasePoolNumber(id)) {
      setPool((p) => {
        const numbers = p.numbers.filter((n) => n.id !== id);
        return {
          numbers,
          available: numbers.filter((n) => n.status === "active").length,
          pending: numbers.filter((n) => n.status === "pending").length,
          failed: numbers.filter((n) => n.status === "failed").length,
          monthly_pence: numbers.reduce((s, n) => s + n.monthly_pence, 0),
        };
      });
    } else flash("Could not release that number");
  };

  return (
    <section className="section" style={{ marginTop: "1rem" }}>
      <h2>Number pool</h2>
      <p className="hint">
        Numbers bought here sit in stock, already routed to the platform. When a new tenant picks a number, live pool stock is offered first
        and handed over instantly - no carrier review wait. The pool only shrinks as tenants take numbers; top it up whenever it runs low.
      </p>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", marginBottom: ".75rem" }}>
        <div className="card"><div className="muted small">Ready to hand out</div><div style={{ fontSize: "1.6rem", fontWeight: 600 }}>{pool.available}</div></div>
        <div className="card"><div className="muted small">Awaiting carrier review</div><div style={{ fontSize: "1.6rem", fontWeight: 600 }}>{pool.pending}</div></div>
        <div className="card"><div className="muted small">Monthly cost</div><div style={{ fontSize: "1.6rem", fontWeight: 600 }}>£{(pool.monthly_pence / 100).toFixed(2)}</div></div>
      </div>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      {canBuy && (
        <div className="row" style={{ gap: ".5rem", alignItems: "flex-end", flexWrap: "wrap", marginBottom: ".75rem" }}>
          <label>Area<br />
            <select value={area} onChange={(e) => setArea(e.target.value)}>
              {regions.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
            </select>
          </label>
          <label>How many<br />
            <input type="number" min={1} max={50} value={qty} onChange={(e) => setQty(Math.max(1, Math.min(50, Number(e.target.value) || 1)))} style={{ width: 90 }} />
          </label>
          <button type="button" className="primary" disabled={busy} onClick={buy}>{busy ? "Buying…" : `Buy ${qty} (≈ £${qty}.00/month)`}</button>
        </div>
      )}
      {pool.numbers.length === 0 ? <p className="muted small">No numbers in stock. New tenants order from the carrier and wait for review.</p> : (
        <table>
          <thead><tr><th>Number</th><th>Status</th><th>Bought</th><th>By</th><th /></tr></thead>
          <tbody>
            {pool.numbers.map((n) => (
              <tr key={n.id}>
                <td><strong>{prettyUk(n.e164)}</strong> <span className="muted small"><code>{n.e164}</code></span></td>
                <td>{n.status === "active" ? <span className="pill ok">Ready</span> : n.status === "pending" ? <span className="pill warn">Under review</span> : <span className="pill bad">Failed</span>}</td>
                <td>{when(n.created_at)}</td>
                <td>{n.bought_by ?? "—"}</td>
                <td>{canBuy && <button type="button" onClick={() => release(n.id, n.e164)}>Release</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!canBuy && <p className="muted small">Only platform owners and finance staff can buy or release pool numbers.</p>}
    </section>
  );
}
