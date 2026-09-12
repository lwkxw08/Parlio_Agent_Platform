import Link from "next/link";
import { fetchCalls, ms } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Calls() {
  const calls = (await fetchCalls()) ?? [];
  return (
    <>
      <h1>Calls</h1>
      <table>
        <thead>
          <tr><th>Started</th><th>Caller</th><th>Dialed</th><th>Status</th><th>Pick-up</th><th>Turn p50</th><th>Duration</th></tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr key={c.call_id}>
              <td><Link href={`/calls/${c.call_id}`}>{new Date(c.started_at).toLocaleString("en-GB")}</Link></td>
              <td>{c.caller ?? "—"}</td>
              <td>{c.dialed ?? "—"}</td>
              <td><span className={`pill ${c.status === "failed" ? "bad" : ""}`}>{c.status}</span></td>
              <td>{ms(c.answer_latency_s)}</td>
              <td>{ms(c.latency.p50_s)}</td>
              <td>{c.duration_s == null ? "—" : `${Math.round(c.duration_s)} s`}</td>
            </tr>
          ))}
          {!calls.length && <tr><td colSpan={7} className="muted">No calls yet</td></tr>}
        </tbody>
      </table>
    </>
  );
}
