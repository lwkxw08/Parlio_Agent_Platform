import Link from "next/link";
import { fetchMe } from "@/lib/api";
import SignOut from "./signout";

export const dynamic = "force-dynamic";

export default async function Account() {
  const me = await fetchMe();
  if (!me.ok) {
    return <p className="muted">{me.status === 401 ? <Link href="/login">Sign in</Link> : "API unreachable"}</p>;
  }
  const u = me.data;
  return (
    <>
      <h1>Your account</h1>
      <div className="section">
        <h2>Profile</h2>
        <dl className="kv">
          <dt>Name</dt><dd>{u.name ?? "—"}</dd>
          <dt>Email</dt><dd>{u.email}</dd>
          <dt>User ID</dt><dd className="small muted">{u.user_id}</dd>
          <dt>Sign-in</dt><dd>{u.mode === "dev" ? "Developer mode (no credentials)" : "Supabase (email / Google)"}</dd>
        </dl>
      </div>
      <div className="section">
        <h2>Organisations</h2>
        <table>
          <thead><tr><th>Organisation</th><th>Role</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {u.memberships.map((m) => (
              <tr key={m.tenant_id}>
                <td>{m.tenant_id}</td><td><span className="pill">{m.role}</span></td><td>{m.status}</td>
                <td><Link href={`/team?tenant=${m.tenant_id}`}>Members</Link></td>
              </tr>
            ))}
            {!u.memberships.length && (
              <tr><td colSpan={4} className="muted">No organisation yet — <Link href="/onboarding">set one up</Link>.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="section">
        <h2>Security</h2>
        <p className="hint">
          Passwords, Google sign-in and password resets are managed by the identity provider. Two-factor authentication
          and session management arrive with the Supabase MFA rollout; sign out below to end this session on this device.
        </p>
        <SignOut mode={u.mode} />
      </div>
    </>
  );
}
