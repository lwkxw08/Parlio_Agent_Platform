import Link from "next/link";
import { type SearchHit, callParty, fetchCalls, fetchSites, ms, phone, searchCalls, secs, when } from "@/lib/api";
import { humanize } from "@/app/breakdown";
import { ExportCallsButton } from "./export-button";

export const dynamic = "force-dynamic";

const KINDS = [
  ["", "All"],
  ["unread", "Unread"],
  ["answered", "Answered"],
  ["missed", "Missed"],
  ["outbound", "Outbound"],
  ["transferred", "Transferred"],
  ["ticketed", "Ticketed"],
  ["escalated", "Urgent"],
  ["blocked", "Blocked / spam"],
] as const;

function outcomeLabel(c: { kind: string; end_reason: string | null }): string {
  if (c.kind !== "blocked") return humanize(c.kind);
  if (c.end_reason === "spam") return "Spam";
  if (c.end_reason === "screened") return "Screened out";
  return "Blocked";
}

type Search = { kind?: string; day?: string; hour?: string; q?: string; site?: string; search?: string };

function range(day?: string) {
  if (!day) return {};
  const since = new Date(`${day}T00:00:00`);
  const until = new Date(since.getTime() + 86_400_000);
  return { since: since.toISOString(), until: until.toISOString() };
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

function momentHref(hit: SearchHit, m: SearchHit["moments"][number]) {
  const p = new URLSearchParams({ tab: m.recording_index != null ? "recording" : "transcript", seq: String(m.seq) });
  if (m.recording_index != null && m.offset_s != null) {
    p.set("rec", String(m.recording_index));
    p.set("t", String(m.offset_s));
  }
  return `/calls/${hit.call_id}?${p.toString()}`;
}

export default async function Calls({ searchParams }: { searchParams: Promise<Search> }) {
  const sp = await searchParams;
  const searching = (sp.search ?? "").trim().length >= 2;
  const [calls, sites, results] = await Promise.all([
    searching ? Promise.resolve([]) : fetchCalls({ kind: sp.kind, hour: sp.hour, q: sp.q, site: sp.site, limit: 200, ...range(sp.day) }),
    fetchSites(),
    searching ? searchCalls({ q: sp.search!.trim(), site: sp.site, limit: 100, ...range(sp.day) }) : Promise.resolve(null),
  ]);
  const href = (patch: Partial<Search>) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...sp, ...patch })) if (v) p.set(k, v);
    const s = p.toString();
    return `/calls${s ? `?${s}` : ""}`;
  };
  const siteName = (id: string | null) => sites?.find((s) => s.id === id)?.name ?? null;

  return (
    <>
      <div className="row between" style={{ marginBottom: "1rem" }}>
        <h1 style={{ margin: 0 }}>Calls</h1>
        <ExportCallsButton />
      </div>
      <div className="chips" style={{ marginBottom: "0.8rem" }}>
        {KINDS.map(([k, label]) => (
          <Link key={k} href={href({ kind: k || undefined })} className={(sp.kind ?? "") === k ? "active" : ""}>{label}</Link>
        ))}
      </div>
      {!!sites?.length && (
        <div className="chips" style={{ marginBottom: "0.8rem" }} aria-label="Location">
          <span className="muted small" style={{ alignSelf: "center" }}>Location:</span>
          <Link href={href({ site: undefined })} className={!sp.site ? "active" : ""}>All locations</Link>
          {sites.map((s) => (
            <Link key={s.id} href={href({ site: s.id })} className={sp.site === s.id ? "active" : ""} title={s.numbers.map(phone).join(", ")}>{s.name}</Link>
          ))}
          <Link href="/sites" className="small" style={{ marginLeft: "auto" }}>Manage locations</Link>
        </div>
      )}
      <form className="filters" action="/calls" method="get">
        {sp.kind && <input type="hidden" name="kind" value={sp.kind} />}
        {sp.site && <input type="hidden" name="site" value={sp.site} />}
        <input type="date" name="day" defaultValue={sp.day} />
        <select name="hour" defaultValue={sp.hour ?? ""}>
          <option value="">Any hour</option>
          {Array.from({ length: 24 }, (_, h) => (
            <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>
          ))}
        </select>
        <input name="q" placeholder="Filter by caller or summary" defaultValue={sp.q} />
        <input name="search" placeholder="Search what was said, e.g. boiler" defaultValue={sp.search} title="Full-text search across transcripts and summaries" />
        <button className="ghost">{searching ? "Search" : "Filter"}</button>
        {(sp.day || sp.hour || sp.q || sp.search) && <Link href={href({ day: undefined, hour: undefined, q: undefined, search: undefined })} className="small">Clear</Link>}
      </form>

      {searching ? (
        <SearchResults query={sp.search!.trim()} results={results && results.ok ? results.data.hits : null} error={results && !results.ok ? results.error : null} siteName={siteName} />
      ) : (
        <table>
          <thead>
            <tr><th>Started</th><th>Who</th>{!!sites?.length && <th>Location</th>}<th>Outcome</th><th>Summary</th><th>Time to answer</th><th>Assistant response</th><th>Duration</th></tr>
          </thead>
          <tbody>
            {(calls ?? []).map((c) => (
              <tr key={c.call_id} className={c.read ? "" : "unread"}>
                <td><Link href={`/calls/${c.call_id}`}>{when(c.started_at)}</Link></td>
                <td>
                  {c.direction === "outbound" && <span className="pill accent" style={{ marginRight: 6 }} title={`The assistant rang ${phone(c.party)}`}>Outbound</span>}
                  {callParty(c)}
                  {typeof c.extracted.name === "string" && c.party && <span className="muted small" style={{ marginLeft: 6 }}>{phone(c.party)}</span>}
                  {c.caller_type === "returning" && <span className="pill" style={{ marginLeft: 4 }}>returning</span>}
                </td>
                {!!sites?.length && <td className="small muted">{c.direction === "inbound" ? sites.find((s) => s.numbers.some((n) => n.replace("+", "") === (c.dialed ?? "").replace("+", "")))?.name ?? "—" : "—"}</td>}
                <td>
                  <span className={`pill ${c.kind === "missed" || c.kind === "blocked" ? "bad" : c.kind === "answered" ? "ok" : ""}`}>{outcomeLabel(c)}</span>
                  {c.escalated && <span className="pill urgent" style={{ marginLeft: 4 }}>urgent</span>}
                </td>
                <td className="small muted">{c.summary ?? "—"}</td>
                <td>{ms(c.answer_latency_s)}</td>
                <td>{ms(c.latency.p50_s)}</td>
                <td>{secs(c.duration_s)}</td>
              </tr>
            ))}
            {!calls?.length && <tr><td colSpan={sites?.length ? 8 : 7} className="muted">No calls match</td></tr>}
          </tbody>
        </table>
      )}
    </>
  );
}

function SearchResults({ query, results, error, siteName }: {
  query: string; results: SearchHit[] | null; error: string | null; siteName: (id: string | null) => string | null;
}) {
  if (error) return <p className="muted">Search failed: {error}</p>;
  if (!results) return <p className="muted">Searching…</p>;
  return (
    <div className="section">
      <div className="row between" style={{ alignItems: "baseline" }}>
        <h2 style={{ margin: 0 }}>{results.length ? `${results.length} call${results.length === 1 ? "" : "s"} mentioning “${query}”` : `No calls mention “${query}”`}</h2>
        <span className="muted small">Click a moment to jump to it in the recording</span>
      </div>
      {results.map((h) => (
        <div key={h.call_id} className="card" style={{ marginTop: "0.8rem" }}>
          <div className="row between" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
            <div>
              <Link href={`/calls/${h.call_id}`}><strong>{h.party ? phone(h.party) : h.direction === "outbound" ? "Outbound call" : "Unknown caller"}</strong></Link>
              <span className="muted small" style={{ marginLeft: 8 }}>{when(h.started_at)} · {secs(h.duration_s)}</span>
              {h.site_id && <span className="pill" style={{ marginLeft: 6 }}>{siteName(h.site_id) ?? h.site_name}</span>}
            </div>
            <div>
              <span className={`pill ${h.kind === "missed" || h.kind === "blocked" ? "bad" : h.kind === "answered" ? "ok" : ""}`}>{humanize(h.kind)}</span>
              <span className="muted small" style={{ marginLeft: 8 }}>{h.total_matches} match{h.total_matches === 1 ? "" : "es"}{h.recordings ? "" : " · no recording"}</span>
            </div>
          </div>
          {h.summary && <p className={`small ${h.summary_matched ? "" : "muted"}`} style={{ margin: "0.4rem 0" }}>{h.summary}</p>}
          {!!h.moments.length && (
            <ul className="small" style={{ margin: 0, paddingLeft: "1.1rem" }}>
              {h.moments.map((m) => (
                <li key={m.seq} style={{ margin: "0.2rem 0" }}>
                  <Link href={momentHref(h, m)} title={m.recording_index != null ? "Play from this moment" : "Show in transcript"}>
                    {m.offset_s != null ? <code style={{ marginRight: 6 }}>{clock(m.offset_s)}</code> : null}
                    <span className="muted">{m.role === "user" ? "Caller" : m.role === "assistant" ? "Assistant" : humanize(m.role)}:</span> {m.snippet}
                  </Link>
                </li>
              ))}
              {h.total_matches > h.moments.length + (h.summary_matched ? 1 : 0) && <li className="muted">…and more in the transcript</li>}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
