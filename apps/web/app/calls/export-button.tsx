"use client";

import { useState } from "react";
import { downloadCsv } from "@/lib/api";

/** Download every call (summary + extracted details) as CSV for the current organisation. */
export function ExportCallsButton() {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setState("busy");
    const err = await downloadCsv("calls");
    setError(err);
    setState(err ? "error" : "done");
    if (!err) setTimeout(() => setState("idle"), 2000);
  };
  return (
    <span className="row" style={{ gap: "0.5rem", alignItems: "center" }}>
      <button className="ghost" onClick={run} disabled={state === "busy"} title="Every call with its summary and extracted details (name, phone, email, reason, booking…)">
        {state === "busy" ? "Preparing…" : state === "done" ? "Downloading…" : "Export CSV"}
      </button>
      {state === "error" && <span className="small muted">Export failed: {error}</span>}
    </span>
  );
}
