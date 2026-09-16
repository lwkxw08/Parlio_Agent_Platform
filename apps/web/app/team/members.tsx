"use client";

import { useState } from "react";
import { type Member, del, patch, post } from "@/lib/api";
import { humanize } from "@/app/breakdown";

type TenantRole = "owner" | "admin" | "member" | "viewer";
const ROLES: TenantRole[] = ["owner", "admin", "member", "viewer"];
const ROLE_HELP: Partial<Record<Member["role"], string>> = {
  owner: "Full control incl. billing and deleting the organisation",
  admin: "Manage assistant, team and settings",
  member: "Handle calls, tickets and contacts",
  viewer: "Read-only access",
};

export default function Members({ tenant, initial, me, canManage }: { tenant: string; initial: Member[]; me: string; canManage: boolean }) {
  const [members, setMembers] = useState(initial);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<TenantRole>("member");
  const [msg, setMsg] = useState<string | null>(null);

  const reload = async () => {
    const r = await fetch(`/team?tenant=${tenant}`, { cache: "no-store" });
    if (r.ok) window.location.reload();
  };

  const invite = async (e: React.FormEvent) => {
    e.preventDefault();
    const m = await post<Member>(`/v1/organisations/${tenant}/members`, { email, name: name || null, role });
    if (!m) return setMsg("Invite failed — check the address and your permissions");
    setMembers((ms) => [...ms.filter((x) => x.user_id !== m.user_id), m]);
    setEmail(""); setName(""); setMsg(`Invitation recorded for ${m.email}. They join automatically on first sign-in.`);
  };

  const changeRole = async (m: Member, r: TenantRole) => {
    const upd = await patch<Member>(`/v1/organisations/${tenant}/members/${m.user_id}`, { role: r });
    if (upd) setMembers((ms) => ms.map((x) => (x.user_id === upd.user_id ? upd : x)));
    else setMsg("Role change refused");
  };

  const remove = async (m: Member) => {
    if (!confirm(`Remove ${m.email} from ${tenant}?`)) return;
    if (await del(`/v1/organisations/${tenant}/members/${m.user_id}`)) setMembers((ms) => ms.filter((x) => x.user_id !== m.user_id));
    else setMsg("Removal refused (the last owner cannot be removed)");
    if (m.user_id === me) reload();
  };

  return (
    <>
      <div className="section">
        <h2>Members of {tenant}</h2>
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.user_id}>
                <td>{m.name ?? "—"}{m.user_id === me && <span className="muted small"> (you)</span>}</td>
                <td>{m.email}</td>
                <td>
                  {canManage ? (
                    <select value={m.role} onChange={(e) => changeRole(m, e.target.value as TenantRole)} title={ROLE_HELP[m.role]}>
                      {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : <span className="pill">{humanize(m.role)}</span>}
                </td>
                <td><span className={`pill ${m.status === "active" ? "ok" : "warn"}`}>{humanize(m.status)}</span></td>
                <td>{canManage && <button className="danger" onClick={() => remove(m)}>Remove</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canManage && (
        <div className="section">
          <h2>Invite a teammate</h2>
          <p className="hint">They can sign in with Google or email using the address below; the invitation activates on their first sign-in.</p>
          <form className="form" onSubmit={invite}>
            <div className="two">
              <label>Email <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
              <label>Name (optional) <input value={name} onChange={(e) => setName(e.target.value)} /></label>
            </div>
            <label>Role
              <select value={role} onChange={(e) => setRole(e.target.value as TenantRole)}>
                {ROLES.filter((r) => r !== "owner").map((r) => <option key={r} value={r}>{r} — {ROLE_HELP[r]}</option>)}
              </select>
            </label>
            <div><button className="primary">Send invitation</button></div>
          </form>
        </div>
      )}
      {msg && <p className="muted small">{msg}</p>}
    </>
  );
}
