import Link from "next/link";
import { fetchTrustCentre, when } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const t = await fetchTrustCentre();
  if (!t) return <><h1>Trust centre</h1><p className="muted">Service unreachable</p></>;
  return (
    <>
      <h1>Trust centre</h1>
      <p className="hint">How ParlioTec handles your callers&apos; data. Updated {when(t.updated_at)} · live service status at <Link href={t.status_url}>/status</Link>.</p>
      <div className="grid">
        <div className="card"><div className="label">Data residency</div><div className="value" style={{ fontSize: "1.05rem" }}>{t.data_residency}</div></div>
        <div className="card"><div className="label">Sub-processors</div><div className="value">{t.sub_processors.length}</div><div className="small muted">listed below with purpose and region</div></div>
      </div>
      {t.sections.map((s) => (
        <div className="section" key={s.id} id={s.id}>
          <h2>{s.title}</h2>
          {s.body.split("\n").filter(Boolean).map((p, i) => <p key={i} className="small" style={{ margin: "0.3rem 0" }}>{p}</p>)}
        </div>
      ))}
      <div className="section" id="sub-processors">
        <h2>Sub-processors</h2>
        <table>
          <thead><tr>{Object.keys(t.sub_processors[0] ?? {}).map((k) => <th key={k} style={{ textTransform: "capitalize" }}>{k.replace(/_/g, " ")}</th>)}</tr></thead>
          <tbody>
            {t.sub_processors.map((sp, i) => <tr key={i}>{Object.values(sp).map((v, j) => <td key={j} className="small">{v}</td>)}</tr>)}
          </tbody>
        </table>
      </div>
    </>
  );
}
