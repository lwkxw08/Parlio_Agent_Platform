"use client";

import { useState } from "react";
import { type BillingPlatformSettings, type StripeMode, setStripeMode, when } from "@/lib/api";

const LABEL: Record<StripeMode, string> = { sandbox: "Sandbox (test cards, no real charges)", live: "Live (real customers, real charges)" };

type Props = { settings: BillingPlatformSettings; isOwner: boolean };

/** Which Stripe account checkouts, invoices and refunds go through. */
export default function StripeModeCard({ settings: initial, isOwner }: Props) {
  const [s, setS] = useState(initial);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 6000); };

  if (s.provider !== "stripe") {
    return (
      <section className="section" style={{ marginTop: "1rem" }}>
        <h2>Stripe mode</h2>
        <p className="hint">Billing on this server is <strong>{s.provider}</strong> — no Stripe keys are configured, so no card is ever charged.</p>
      </section>
    );
  }

  const change = async (mode: StripeMode) => {
    if (mode === s.stripe_mode || busy) return;
    if (mode === "live" && !confirm("Switch to LIVE Stripe? New checkouts will charge real cards. Existing sandbox subscriptions will not be recognised by the live account.")) return;
    setBusy(true);
    const r = await setStripeMode(mode);
    setBusy(false);
    if (!r.ok) return flash(r.error);
    setS(r.data);
    flash(`Stripe mode is now ${r.data.stripe_mode}`);
  };

  return (
    <section className="section" style={{ marginTop: "1rem" }}>
      <h2>Stripe mode</h2>
      <p className="hint">
        Sandbox uses your Stripe test account (4242 cards) so checkout and webhooks can be exercised without money moving. Live charges real
        customers. Webhooks from the inactive account are ignored, and every switch is written to the audit log.
      </p>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      <div className="row" style={{ gap: ".5rem", flexWrap: "wrap" }}>
        {(["sandbox", "live"] as StripeMode[]).map((m) => {
          const configured = s.available_modes.includes(m);
          const active = s.stripe_mode === m;
          return (
            <button
              key={m}
              type="button"
              className={active ? "primary" : ""}
              disabled={!isOwner || !configured || busy}
              onClick={() => change(m)}
              title={configured ? LABEL[m] : `${m} keys are not configured on this server`}
            >
              {active ? "● " : ""}{LABEL[m]}{configured ? "" : " — not configured"}
            </button>
          );
        })}
      </div>
      {s.updated_by && s.updated_at && <p className="muted small">Last changed by {s.updated_by} · {when(s.updated_at)}</p>}
      {!isOwner && <p className="muted small">Only platform owners can switch mode.</p>}
    </section>
  );
}
