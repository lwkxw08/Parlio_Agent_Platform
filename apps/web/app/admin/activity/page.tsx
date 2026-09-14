import { fetchAdminActivity, when } from "@/lib/api";
import { TenantLink } from "../ui";

export const dynamic = "force-dynamic";

export default async function Page() {
  const rows = await fetchAdminActivity(300);
  if (!rows) return <p className="muted">API unreachable</p>;
  return (
    <>
      <p className="hint muted small">Every platform-staff action is recorded here (and in the affected tenant&apos;s own audit log).</p>
      <table>
        <thead><tr><th>When</th><th>Staff</th><th>Role</th><th>Action</th><th>Tenant</th><th>Target</th><th>IP</th></tr></thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td className="small">{when(e.at)}</td>
              <td>{e.actor}</td>
              <td className="small">{String(e.meta.staff_role ?? "")}</td>
              <td><code>{e.action}</code></td>
              <td>{e.tenant_id === "parlio-platform" ? <span className="muted">platform</span> : <TenantLink id={e.tenant_id} />}</td>
              <td className="small">{e.target ?? "—"}</td>
              <td className="small muted">{e.ip ?? ""}</td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={7} className="muted">No staff activity yet.</td></tr>}
        </tbody>
      </table>
    </>
  );
}
