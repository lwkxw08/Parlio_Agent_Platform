"use client";

import { useState } from "react";
import { type Member, type StaffRole, type StaffSettings, del, put, request, when } from "@/lib/api";

const ROLES: [StaffRole, string][] = [
  ["owner", "Owner — everything, incl. staff & plans"],
  ["finance", "Finance — plans, coupons, subscriptions, credits, refunds"],
  ["support", "Support — tenants, flags, notes, view-as, status banner"],
  ["readonly", "Read-only — view everything, change nothing"],
];

type Props = { staff: Member[]; settings: StaffSettings; isOwner: boolean; selfId: string | null };

export default function Staff({ staff: initial, settings: initialSettings, isOwner, selfId }: Props) {
  const [staff, setStaff] = useState<Member[]>(initial);
  const [settings, setSettings] = useState<StaffSettings>(initialSettings);
  const [allow, setAllow] = useState(initialSettings.ip_allowlist.join("\n"));
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<StaffRole>("support");
  const [msg, setMsg] = useState<string | null>(null);
  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(null), 6000); };

  const invite = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await request<Member>("/v1/admin/staff", { method: "POST", body: JSON.stringify({ email, name: name || null, role }) });
    if (!r.ok) return flash(r.error);
    setStaff((s) => [...s.filter((m) => m.user_id !== r.data.user_id), r.data]);
    setEmail(""); setName("");
    flash(`Invited ${r.data.email} as ${r.data.role}`);
  };
  const setRoleFor = async (m: Member, next: StaffRole) => {
    const r = await request<Member>(`/v1/admin/staff/${m.user_id}`, { method: "PATCH", body: JSON.stringify({ role: next }) });
    if (!r.ok) return flash(r.error);
    setStaff((s) => s.map((x) => (x.user_id === m.user_id ? r.data : x)));
  };
  const remove = async (m: Member) => {
    if (!confirm(`Remove ${m.email} from platform staff?`)) return;
    if (await del(`/v1/admin/staff/${m.user_id}`)) setStaff((s) => s.filter((x) => x.user_id !== m.user_id));
    else flash("Could not remove (last owner cannot be removed)");
  };
  const saveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    const body = { ...settings, ip_allowlist: allow.split(/[\n,\s]+/).map((s) => s.trim()).filter(Boolean) };
    const r = await put<StaffSettings>("/v1/admin/staff/settings", body);
    if (!r.ok) return flash(r.error);
    setSettings(r.data);
    setAllow(r.data.ip_allowlist.join("\n"));
    flash("Staff settings saved");
  };

  return (
    <>
      {msg && <p className="small" style={{ color: "var(--accent)" }}>{msg}</p>}
      <div className="grid" style={{ gridTemplateColumns: "2fr 1fr" }}>
        <div className="section">
          <h2>Platform staff</h2>
          <p className="hint">Staff accounts are separate from tenant memberships and must use two-factor authentication. Only owners can change staff.</p>
          <table>
            <thead><tr><th>Email</th><th>Role</th><th>Status</th>{isOwner && <th />}</tr></thead>
            <tbody>
              {staff.map((m) => (
                <tr key={m.user_id}>
                  <td>{m.email}<div className="small muted">{m.name ?? ""}{m.user_id === selfId ? " (you)" : ""}</div></td>
                  <td>
                    {isOwner && m.user_id !== selfId ? (
                      <select value={m.role} onChange={(e) => setRoleFor(m, e.target.value as StaffRole)}>
                        {ROLES.map(([r]) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    ) : m.role}
                  </td>
                  <td><span className={`pill ${m.status === "active" ? "ok" : "warn"}`}>{m.status}</span></td>
                  {isOwner && <td>{m.user_id !== selfId && <button type="button" className="ghost" onClick={() => remove(m)}>Remove</button>}</td>}
                </tr>
              ))}
            </tbody>
          </table>
          {isOwner && (
            <form className="form" onSubmit={invite} style={{ marginTop: "1rem" }}>
              <h2>Invite staff</h2>
              <div className="two">
                <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
                <label>Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
              </div>
              <label>Role
                <select value={role} onChange={(e) => setRole(e.target.value as StaffRole)}>
                  {ROLES.map(([r, label]) => <option key={r} value={r}>{label}</option>)}
                </select>
              </label>
              <button type="submit" className="primary">Send invite</button>
            </form>
          )}
        </div>
        <form className="section form" onSubmit={saveSettings}>
          <h2>Access controls</h2>
          <p className="hint">Restrict the admin console to office/VPN IPs (CIDR or single addresses, one per line). Blank = any IP. Your current IP must be included or the save is rejected.</p>
          <fieldset disabled={!isOwner} style={{ border: 0, padding: 0, margin: 0, display: "contents" }}>
            <label>IP allow-list<textarea value={allow} onChange={(e) => setAllow(e.target.value)} rows={5} placeholder={"203.0.113.10\n198.51.100.0/24"} /></label>
            <label>View-as session length (minutes)<input type="number" min={5} max={480} value={settings.view_as_ttl_minutes} onChange={(e) => setSettings({ ...settings, view_as_ttl_minutes: Number(e.target.value) })} /></label>
            {isOwner && <button type="submit" className="primary">Save</button>}
          </fieldset>
          {settings.updated_by && <p className="muted small">Last changed by {settings.updated_by} · {when(settings.updated_at)}</p>}
        </form>
      </div>
    </>
  );
}
