import Link from "next/link";
import { type RequiredField, fetchAssistants, fetchVersions, request } from "@/lib/api";
import NewAssistant from "./new-assistant";
import Studio from "./studio";

export const dynamic = "force-dynamic";

export default async function AssistantPage({ searchParams }: { searchParams: Promise<{ id?: string }> }) {
  const sp = await searchParams;
  const assistants = (await fetchAssistants()) ?? [];
  const current = assistants.find((a) => a.assistant_id === sp.id) ?? assistants[0];
  if (!current) {
    return (
      <>
        <h1>Assistant Studio</h1>
        <p className="muted">No assistant yet — <Link href="/onboarding">run the setup wizard</Link>.</p>
      </>
    );
  }
  const [versions, fields] = await Promise.all([
    fetchVersions(current.assistant_id),
    request<RequiredField[]>(`/v1/assistants/${current.assistant_id}/required-fields`),
  ]);
  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: "1rem", flexWrap: "wrap" }}>
        <h1 style={{ margin: 0 }}>Assistant Studio</h1>
        {assistants.length > 1 && (
          <span className="chips">
            {assistants.map((a) => <Link key={a.assistant_id} href={`/assistant?id=${a.assistant_id}`} className={a.assistant_id === current.assistant_id ? "active" : ""}>{a.name} · {a.business_name}</Link>)}
          </span>
        )}
        <span style={{ marginLeft: "auto" }}><NewAssistant assistants={assistants.filter((a) => a.tenant_id === current.tenant_id)} /></span>
      </div>
      <p className="muted small">{current.business_name} · {current.tenant_id} · version {current.assistant_version}</p>
      <Studio initial={current} versions={versions ?? []} requiredFields={fields.ok ? fields.data : []} />
    </>
  );
}
