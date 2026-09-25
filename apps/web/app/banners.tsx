"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { fetchTrialStatus, type PublicStatus, type TrialStatus, VIEW_AS_COOKIE } from "@/lib/api";

const gb = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long" }) : "");

/** Last-7-days trial banner, grace warning, and a full Subscribe wall once calls are paused. */
export function TrialBanner({ tenant, children }: { tenant: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const [trial, setTrial] = useState<TrialStatus | null>(null);
  useEffect(() => {
    let live = true;
    fetchTrialStatus(tenant).then((t) => { if (live) setTrial(t); });
    return () => { live = false; };
  }, [tenant, pathname]);
  const billing = `/billing?tenant=${tenant}&tab=plan`;
  const onBilling = pathname.startsWith("/billing") || pathname.startsWith("/account") || pathname.startsWith("/login");
  if (!trial || trial.state === "none") return <>{children}</>;
  if ((trial.state === "paused" || trial.state === "closed") && !onBilling) {
    const closed = trial.state === "closed";
    return (
      <section className="card trial-wall" role="alert">
        <h1>{closed ? "Your trial account has been closed" : "Your free trial has ended"}</h1>
        <p>
          {closed
            ? "The account was closed after 30 days without a plan and its ParlioTec number released. Your data is retained per our retention policy - choose a plan to reopen the account."
            : `Calls to your ParlioTec number are paused and callers hear a short "temporarily unavailable" message. Nothing has been deleted - choose a plan and calls resume immediately.${trial.closes_at ? ` Without a plan the account closes on ${gb(trial.closes_at)}.` : ""}`}
        </p>
        <p><Link className="btn" href={billing}>Choose a plan</Link></p>
        <p className="small muted">The dashboard is read-only until then.</p>
      </section>
    );
  }
  let banner: React.ReactNode = null;
  if (trial.state === "grace") {
    banner = (
      <div className="banner bad" role="status">
        <strong>Your free trial ended {gb(trial.trial_ends_at)}</strong>
        <span>Calls are still answered until {gb(trial.grace_ends_at)}, then paused until you choose a plan.</span>
        <Link className="btn" href={billing}>Subscribe</Link>
      </div>
    );
  } else if (trial.state === "trialing" && trial.days_left <= 7) {
    banner = (
      <div className="banner warn" role="status">
        <strong>{trial.days_left <= 1 ? "Your free trial ends today" : `${trial.days_left} days left on your free trial`}</strong>
        <span>Choose a plan before {gb(trial.trial_ends_at)} to keep your assistant answering. No card has been taken.</span>
        <Link className="btn" href={billing}>Subscribe</Link>
      </div>
    );
  }
  return (
    <>
      {banner}
      {children}
    </>
  );
}

export function StatusBanner({ status }: { status: PublicStatus }) {
  const cls = status.level === "incident" ? "bad" : status.level === "maintenance" ? "" : "warn";
  return (
    <div className={`banner ${cls}`} role="status">
      <strong>{status.title || status.level}</strong>
      {status.message && <span>{status.message}</span>}
      {status.link && <a href={status.link} target="_blank" rel="noreferrer">More info</a>}
    </div>
  );
}

export function ViewAsBanner({ tenant }: { tenant: string }) {
  const router = useRouter();
  const exit = () => {
    document.cookie = `${VIEW_AS_COOKIE}=; path=/; max-age=0`;
    router.push(`/admin/tenants/${tenant}`);
    router.refresh();
  };
  return (
    <div className="banner warn" role="status">
      <strong>Viewing as {tenant}</strong>
      <span>Read-only support view — every request is logged.</span>
      <button type="button" className="ghost" onClick={exit}>Exit</button>
    </div>
  );
}
