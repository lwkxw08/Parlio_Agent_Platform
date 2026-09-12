import Link from "next/link";
import { fetchOverview, pct, secs, ms } from "@/lib/api";

export const dynamic = "force-dynamic";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Bars({ values, labels, tall }: { values: number[]; labels: string[]; tall?: boolean }) {
  const max = Math.max(1, ...values);
  return (
    <div className={`bars ${tall ? "tall" : ""}`} style={{ marginBottom: "1.4rem" }}>
      {values.map((v, i) => (
        <div key={i} className="bar" style={{ height: `${(v / max) * 100}%` }} title={`${labels[i]}: ${v}`}>
          <span>{labels[i]}</span>
        </div>
      ))}
    </div>
  );
}

function Delta({ v }: { v: number | null | undefined }) {
  if (v == null) return null;
  return <span className={`delta ${v >= 0 ? "up" : "down"}`}>{v >= 0 ? "▲" : "▼"} {Math.abs(v)}%</span>;
}

export default async function Analytics({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const sp = await searchParams;
  const days = Number(sp.days ?? 30);
  const a = await fetchOverview({ days });
  if (!a) return <><h1>Analytics</h1><p className="muted">API unreachable</p></>;
  const c = a.current;
  const p = a.previous;

  return (
    <>
      <h1>Analytics</h1>
      <div className="chips" style={{ marginBottom: "1rem" }}>
        {[7, 30, 90].map((d) => <Link key={d} href={`/analytics?days=${d}`} className={days === d ? "active" : ""}>Last {d} days</Link>)}
        <span className="muted small">vs previous {days} days · {a.timezone}</span>
      </div>

      <div className="grid">
        <div className="card"><div className="label">Total calls</div><div className="value">{c.total_calls}<Delta v={a.change.total_calls} /></div><div className="small muted">prev {p.total_calls}</div></div>
        <div className="card"><div className="label">Answer rate</div><div className="value">{pct(c.answer_rate)}<Delta v={a.change.answer_rate} /></div><div className="small muted">{c.missed} missed · {c.blocked} blocked</div></div>
        <div className="card"><div className="label">Avg duration</div><div className="value">{secs(c.avg_duration_s)}<Delta v={a.change.avg_duration_s} /></div><div className="small muted">{Math.round(c.total_minutes)} min total</div></div>
        <div className="card"><div className="label">Avg pick-up</div><div className="value">{ms(c.avg_answer_latency_s)}</div></div>
        <div className="card"><div className="label">Callers</div><div className="value">{c.unique_callers}<Delta v={a.change.unique_callers} /></div><div className="small muted">{c.avg_calls_per_caller ?? "—"} calls / caller</div></div>
        <div className="card"><div className="label">New callers</div><div className="value">{c.new_callers}<Delta v={a.change.new_callers} /></div></div>
        <div className="card"><div className="label">Transferred</div><div className="value">{c.transferred}</div><div className="small muted">{pct(a.transfers.answer_rate)} reached a human</div></div>
        <div className="card"><div className="label">Tickets</div><div className="value">{c.ticketed}</div><div className="small muted">{a.tickets.open} open · {a.tickets.sla_breached} SLA breached</div></div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <div className="card">
          <h2>Daily calls</h2>
          <Bars tall values={a.daily.map((d) => d.calls)} labels={a.daily.map((d) => d.day.slice(8))} />
        </div>
        <div className="card">
          <h2>By day of week</h2>
          <Bars tall values={a.by_weekday} labels={DAYS} />
        </div>
      </div>

      <div className="card" style={{ marginBottom: "1rem" }}>
        <h2>Volume by hour</h2>
        <Bars values={a.by_hour} labels={a.by_hour.map((_, h) => (h % 3 === 0 ? String(h).padStart(2, "0") : ""))} />
      </div>

      <div className="grid">
        <div className="card">
          <h2>Prospects & customers</h2>
          <dl className="kv">
            <dt>Contacts</dt><dd>{a.prospects.contacts}</dd>
            <dt>Prospects</dt><dd>{a.prospects.prospects}</dd>
            <dt>Customers</dt><dd>{a.prospects.customers}</dd>
            <dt>VIP</dt><dd>{a.prospects.vip}</dd>
            <dt>Returning callers</dt><dd>{a.prospects.returning_callers} ({pct(a.prospects.returning_rate)})</dd>
          </dl>
          {a.prospects.top_callers.length > 0 && (
            <>
              <p className="small muted" style={{ marginBottom: 4 }}>Most frequent</p>
              <ul className="small">{a.prospects.top_callers.map((t) => <li key={t.e164}>{t.name || t.e164} — {t.calls} calls</li>)}</ul>
            </>
          )}
        </div>
        <div className="card">
          <h2>Usage this month ({a.usage.month})</h2>
          <dl className="kv">
            <dt>Calls</dt><dd>{a.usage.calls}</dd>
            <dt>Minutes</dt><dd>{Math.round(a.usage.minutes)}</dd>
            <dt>Transfers</dt><dd>{a.usage.transfers}</dd>
            <dt>Tickets</dt><dd>{a.usage.tickets}</dd>
          </dl>
        </div>
        <div className="card">
          <h2>Quality signals</h2>
          <p className="small muted" style={{ margin: "0 0 4px" }}>Information not captured</p>
          <ul className="small">
            {a.top_missed_fields.map((m) => <li key={m.field}>{m.field.replaceAll("_", " ")} — {m.count}</li>)}
            {!a.top_missed_fields.length && <li className="muted">none</li>}
          </ul>
          <p className="small muted" style={{ margin: "8px 0 4px" }}>Feedback flags</p>
          <ul className="small">
            {Object.entries(a.feedback_by_type).map(([k, v]) => <li key={k}>{k.replaceAll("_", " ")} — {v}</li>)}
            {!Object.keys(a.feedback_by_type).length && <li className="muted">none</li>}
          </ul>
        </div>
        <div className="card">
          <h2>Handoff</h2>
          <dl className="kv">
            <dt>Transfers</dt><dd>{a.transfers.total}</dd>
            {Object.entries(a.transfers.by_outcome).map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>· {k}</dt><dd>{v}</dd></div>)}
            <dt>Tickets resolved</dt><dd>{a.tickets.resolved}</dd>
            <dt>Time to claim</dt><dd>{secs(a.tickets.avg_time_to_claim_s)}</dd>
            <dt>Time to resolve</dt><dd>{secs(a.tickets.avg_time_to_resolve_s)}</dd>
          </dl>
          <p className="small"><Link href="/handoff">Full handoff analytics →</Link></p>
        </div>
      </div>
    </>
  );
}
