import type { ComponentState } from "@/lib/api";

export const STATE_LABEL: Record<ComponentState, string> = {
  operational: "Operational", degraded: "Degraded performance", partial_outage: "Partial outage", major_outage: "Major outage", maintenance: "Maintenance",
};
export const stateCls = (s: ComponentState) => (s === "operational" ? "ok" : s === "major_outage" ? "bad" : "warn");
