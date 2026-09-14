import Link from "next/link";
import { type RoadmapStatus, ROADMAP_LABEL, fetchPublicRoadmap } from "@/lib/api";

export const dynamic = "force-dynamic";
const ORDER: RoadmapStatus[] = ["in_progress", "planned", "considering", "shipped"];

export default async function Page() {
  const items = await fetchPublicRoadmap();
  if (!items) return <><h1>Roadmap</h1><p className="muted">Service unreachable</p></>;
  return (
    <>
      <h1>Roadmap</h1>
      <p className="hint">What we&apos;re building next. Customers can vote and suggest ideas from <Link href="/whats-new">What&apos;s new</Link> in the dashboard. See also the <Link href="/changelog">changelog</Link>.</p>
      {items.length === 0 && <p className="small muted">Nothing published yet.</p>}
      {ORDER.filter((st) => items.some((r) => r.status === st)).map((st) => (
        <div className="section" key={st}>
          <h2>{ROADMAP_LABEL[st]}</h2>
          {items.filter((r) => r.status === st).map((r) => (
            <div className="list-row" key={r.id} style={{ gridTemplateColumns: "1fr auto", alignItems: "center" }}>
              <div>
                <b className="small">{r.title}</b> {r.category && <span className="pill">{r.category}</span>} {r.eta && <span className="small muted">· {r.eta}</span>}
                <div className="small muted">{r.description}</div>
              </div>
              <span className="small muted">▲ {r.votes}</span>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
