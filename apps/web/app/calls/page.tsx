import Link from "next/link";
import { fetchCalls, ms, secs, when } from "@/lib/api";

export const dynamic = "force-dynamic";

const KINDS = [
  ["", "All"],
  ["unread", "Unread"],
  ["answered", "Answered"],
  ["missed", "Missed"],
  ["transferred", "Transferred"],
  ["ticketed", "Ticketed"],
  ["escalated", "Urgent"],
  ["blocked", "Blocked"],
] as const;

type Search = { kind?: string; day?: string; hour?: string; q?: string };

function range(day?: string) {
  if (!day) return {};
  const since = new Date(`${day}T00:00:00`);
  const until = new Date(since.getTime() + 86_400_000);
  return { since: since.toISOString(), until: until.toISOString() };
}

export default async function Calls({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const calls =
    (await fetchCalls({ kind: sp.kind, hour: sp.hour, q: sp.q, limit: 200, ...range(sp.day) })) ?? [];
  const href = (patch: Partial<Search>) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...sp, ...patch })) if (v) p.set(k, v);
    const s = p.toString();
    return `/calls${s ? `?${s}` : ""}`;
  };

  return (
    <>
      <h1>Calls</h1>
      <div className="chips" style={{ marginBottom: "0.8rem" }}>
        {KINDS.map(([k, label]) => (
          <Link key={k} href={href({ kind: k || undefined })} className={(sp.kind ?? "") === k ? "active" : ""}>{label}</Link>
        ))}
      </div>
      <form className="filters" action="/calls" method="get">
        {sp.kind && <input type="hidden" name="kind" value={sp.kind} />}
        <input type="date" name="day" defaultValue={sp.day} />
        <select name="hour" defaultValue={sp.hour ?? ""}>
          <option value="">Any hour</option>
          {Array.from({ length: 24 }, (_, h) => (
            <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>
          ))}
        </select>
        <input name="q" placeholder="Search caller or summary" defaultValue={sp.q} />
        <button className="ghost">Filter</button>
        {(sp.day || sp.hour || sp.q) && <Link href={href({ day: undefined, hour: undefined, q: undefined })} className="small">Clear</Link>}
      </form>
      <table>
        <thead>
          <tr><th>Started</th><th>Caller</th><th>Outcome</th><th>Summary</th><th>Pick-up</th><th>Turn p50</th><th>Duration</th></tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr key={c.call_id} className={c.read ? "" : "unread"}>
              <td><Link href={`/calls/${c.call_id}`}>{when(c.started_at)}</Link></td>
              <td>{c.caller ?? "—"}{c.caller_type === "returning" && <span className="pill" style={{ marginLeft: 4 }}>returning</span>}</td>
              <td>
                <span className={`pill ${c.kind === "missed" || c.kind === "blocked" ? "bad" : c.kind === "answered" ? "ok" : ""}`}>{c.kind}</span>
                {c.escalated && <span className="pill urgent" style={{ marginLeft: 4 }}>urgent</span>}
              </td>
              <td className="small muted">{c.summary ?? "—"}</td>
              <td>{ms(c.answer_latency_s)}</td>
              <td>{ms(c.latency.p50_s)}</td>
              <td>{secs(c.duration_s)}</td>
            </tr>
          ))}
          {!calls.length && <tr><td colSpan={7} className="muted">No calls match</td></tr>}
        </tbody>
      </table>
    </>
  );
}
