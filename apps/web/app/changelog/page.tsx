import Link from "next/link";
import { KIND_LABEL, fetchChangelog, when } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const items = await fetchChangelog();
  if (!items) return <><h1>Changelog</h1><p className="muted">Service unreachable</p></>;
  return (
    <>
      <h1>Changelog</h1>
      <p className="hint">What&apos;s changed in ParlioTec. See also the <Link href="/roadmap">public roadmap</Link>, <Link href="/status">service status</Link> and <Link href="/trust">trust centre</Link>.</p>
      {items.length === 0 && <p className="small muted">No entries yet.</p>}
      {items.map((a) => (
        <div className="section" key={a.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>{a.title}</h2>
            <span className="small muted"><span className="pill">{KIND_LABEL[a.kind] ?? a.kind}</span> {when(a.created_at)}</span>
          </div>
          {a.body.split("\n").filter(Boolean).map((p, i) => <p key={i} className="small" style={{ margin: ".3rem 0" }}>{p}</p>)}
          {a.link && <Link className="small" href={a.link}>Learn more →</Link>}
        </div>
      ))}
    </>
  );
}
