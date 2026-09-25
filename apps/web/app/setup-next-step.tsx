"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { type SetupChecklist, fetchChecklist } from "@/lib/api";

/**
 * "What's next" card shown at the bottom of every setup destination so a new tenant can
 * move through the checklist without going back to find it. `step` is the checklist key
 * this page satisfies; `refreshKey` re-reads the checklist when the page's state changes.
 */
export function SetupNextStep({
  tenant,
  step,
  title,
  done,
  pending,
  refreshKey,
}: {
  tenant: string;
  step: string;
  title?: string;
  done: string;
  pending?: string;
  refreshKey?: unknown;
}) {
  const [checklist, setChecklist] = useState<SetupChecklist | null>(null);
  useEffect(() => {
    fetchChecklist(tenant).then((c) => c && setChecklist(c));
  }, [tenant, refreshKey]);
  if (checklist && checklist.completed >= checklist.total) return null;
  const item = checklist?.items.find((i) => i.key === step);
  const next = checklist?.items.find((i) => !i.done && i.key !== step) ?? null;
  return (
    <div className="card" style={{ marginTop: "1rem", borderColor: "var(--accent)" }}>
      <h3 style={{ margin: 0 }}>{title ?? "What's next"}</h3>
      <p className="small muted">
        {item && !item.done && pending ? pending : done}
        {checklist ? ` You've completed ${checklist.completed} of ${checklist.total} setup steps.` : ""}
      </p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        {next && <Link className="btn primary small" href={next.href}>Next: {next.title}</Link>}
        <Link className="btn small" href="/setup">Back to setup checklist</Link>
      </div>
    </div>
  );
}
