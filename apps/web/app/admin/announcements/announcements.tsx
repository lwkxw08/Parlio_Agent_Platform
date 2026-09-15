"use client";

import { useState } from "react";
import {
  type Announcement, type AnnouncementIn, type AnnouncementKind, type Feedback, type RoadmapItem, type RoadmapItemIn, type RoadmapStatus,
  type WhiteGloveRequest, type WhiteGloveStatus,
  KIND_LABEL, ROADMAP_LABEL, createAnnouncement, createRoadmapItem, deleteAnnouncement, deleteRoadmapItem, setFeedbackStatus,
  updateAnnouncement, updateRoadmapItem, updateWhiteGlove, when,
} from "@/lib/api";
import { humanize } from "@/app/breakdown";

const SECTIONS = ["whiteglove", "announcements", "roadmap", "feedback"] as const;
type Section = (typeof SECTIONS)[number];
const SECTION_LABEL: Record<Section, string> = { whiteglove: "White-glove queue", announcements: "Announcements", roadmap: "Roadmap", feedback: "Feedback" };
const WG_STATUSES: WhiteGloveStatus[] = ["requested", "scheduled", "in_progress", "completed", "cancelled"];

const BLANK_ANN: AnnouncementIn = { title: "", body: "", kind: "feature", pinned: false, published: false, link: null };
const BLANK_RM: RoadmapItemIn = { title: "", description: "", status: "considering", category: "", eta: null };

export default function Announcements({ announcements: a0, roadmap: r0, feedback: f0, whiteglove: w0, staffEmail, canEdit }: {
  announcements: Announcement[]; roadmap: RoadmapItem[]; feedback: Feedback[]; whiteglove: WhiteGloveRequest[]; staffEmail: string; canEdit: boolean;
}) {
  const [section, setSection] = useState<Section>(w0.some((w) => w.status === "requested") ? "whiteglove" : "announcements");
  const [anns, setAnns] = useState(a0);
  const [roadmap, setRoadmap] = useState(r0);
  const [feedback, setFeedback] = useState(f0);
  const [wg, setWg] = useState(w0);
  const [editAnn, setEditAnn] = useState<(AnnouncementIn & { id?: string }) | null>(null);
  const [editRm, setEditRm] = useState<(RoadmapItemIn & { id?: string }) | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});

  const saveAnn = async (e: React.FormEvent) => {
    e.preventDefault(); if (!editAnn) return;
    const { id, ...body } = editAnn;
    const r = id ? await updateAnnouncement(id, body) : await createAnnouncement(body);
    if (!r.ok) return setMsg(r.error);
    setAnns(id ? anns.map((a) => (a.id === id ? r.data : a)) : [r.data, ...anns]);
    setEditAnn(null); setMsg(body.published ? "Published to all tenants and the public changelog" : "Saved as draft");
  };
  const saveRm = async (e: React.FormEvent) => {
    e.preventDefault(); if (!editRm) return;
    const { id, ...body } = editRm;
    const r = id ? await updateRoadmapItem(id, body) : await createRoadmapItem(body);
    if (!r.ok) return setMsg(r.error);
    setRoadmap(id ? roadmap.map((a) => (a.id === id ? r.data : a)) : [r.data, ...roadmap]);
    setEditRm(null); setMsg("Roadmap saved");
  };
  const wgUpdate = async (id: string, body: Parameters<typeof updateWhiteGlove>[1]) => {
    const r = await updateWhiteGlove(id, body);
    if (r) { setWg(wg.map((w) => (w.id === id ? r : w))); setNote({ ...note, [id]: "" }); }
  };

  return (
    <>
      <div className="chips" style={{ marginBottom: "1rem" }}>
        {SECTIONS.map((s) => (
          <button key={s} className={section === s ? "active" : ""} onClick={() => setSection(s)}>
            {SECTION_LABEL[s]}
            {s === "whiteglove" && wg.filter((w) => w.status === "requested").length > 0 && <> <span className="pill warn">{wg.filter((w) => w.status === "requested").length}</span></>}
            {s === "feedback" && feedback.filter((f) => f.status === "new").length > 0 && <> <span className="pill warn">{feedback.filter((f) => f.status === "new").length}</span></>}
          </button>
        ))}
      </div>
      {msg && <p className="small" style={{ color: "var(--ok-fg)" }}>{msg}</p>}

      {section === "whiteglove" && (
        <div className="section">
          <h2>White-glove onboarding requests</h2>
          <p className="hint">Growth+ customers who asked for a guided session. Schedule, assign and note progress; the customer sees status and notes on their Setup page.</p>
          {wg.length === 0 && <p className="small muted">No requests.</p>}
          <table>
            <thead><tr><th>Tenant</th><th>Contact</th><th>Help with</th><th>Preferred times</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {wg.map((w) => (
                <tr key={w.id}>
                  <td><b>{w.tenant_id}</b><div className="small muted">{when(w.created_at)}</div></td>
                  <td className="small">{w.contact_name}<br />{w.contact_email}{w.contact_phone && <><br />{w.contact_phone}</>}</td>
                  <td className="small">{w.areas.join(", ")}{w.notes && <div className="muted">{w.notes}</div>}</td>
                  <td className="small">{w.preferred_slots.join(" · ") || "—"}</td>
                  <td>
                    <select className="field" disabled={!canEdit} value={w.status} onChange={(e) => wgUpdate(w.id, { status: e.target.value as WhiteGloveStatus })}>
                      {WG_STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
                    </select>
                    <div className="small muted">{w.assigned_to ?? "unassigned"}{w.scheduled_at && ` · ${when(w.scheduled_at)}`}</div>
                  </td>
                  <td style={{ minWidth: "16rem" }}>
                    {canEdit && (
                      <form className="form" onSubmit={(e) => { e.preventDefault(); if (note[w.id]?.trim()) wgUpdate(w.id, { note: note[w.id] }); }}>
                        <input placeholder="Add note (visible to customer)" value={note[w.id] ?? ""} onChange={(e) => setNote({ ...note, [w.id]: e.target.value })} />
                        <div className="row">
                          <button className="ghost small">Note</button>
                          <input type="datetime-local" aria-label="Session time" onChange={(e) => { if (e.target.value) wgUpdate(w.id, { scheduled_at: new Date(e.target.value).toISOString(), assigned_to: w.assigned_to ?? staffEmail }); }} />
                          {!w.assigned_to && <button type="button" className="ghost small" onClick={() => wgUpdate(w.id, { assigned_to: staffEmail })}>Assign me</button>}
                        </div>
                      </form>
                    )}
                    {w.staff_notes.length > 0 && <ul className="small" style={{ marginTop: ".4rem" }}>{w.staff_notes.map((n, i) => <li key={i}><span className="muted">{n.author}:</span> {n.text}</li>)}</ul>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section === "announcements" && (
        <div className="section">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Announcements</h2>
            {canEdit && <button className="primary small" onClick={() => setEditAnn(BLANK_ANN)}>+ New</button>}
          </div>
          <p className="hint">Published announcements appear on every tenant&apos;s What&apos;s new page and the public /changelog. Drafts are only visible here.</p>
          {editAnn && (
            <form className="form card" onSubmit={saveAnn} style={{ marginBottom: "1rem" }}>
              <div className="two">
                <label>Title<input required value={editAnn.title} onChange={(e) => setEditAnn({ ...editAnn, title: e.target.value })} /></label>
                <label>Type<select value={editAnn.kind} onChange={(e) => setEditAnn({ ...editAnn, kind: e.target.value as AnnouncementKind })}>{(Object.keys(KIND_LABEL) as AnnouncementKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}</select></label>
              </div>
              <label>Body<textarea required rows={5} value={editAnn.body} onChange={(e) => setEditAnn({ ...editAnn, body: e.target.value })} /></label>
              <label>Link (optional)<input value={editAnn.link ?? ""} onChange={(e) => setEditAnn({ ...editAnn, link: e.target.value || null })} placeholder="/quality or https://…" /></label>
              <label className="check"><input type="checkbox" checked={editAnn.pinned} onChange={(e) => setEditAnn({ ...editAnn, pinned: e.target.checked })} /> Pin to top</label>
              <label className="check"><input type="checkbox" checked={editAnn.published} onChange={(e) => setEditAnn({ ...editAnn, published: e.target.checked })} /> Published</label>
              <div className="row"><button className="primary">Save</button><button type="button" className="ghost" onClick={() => setEditAnn(null)}>Cancel</button></div>
            </form>
          )}
          <table>
            <thead><tr><th>Title</th><th>Type</th><th>Status</th><th>Updated</th><th></th></tr></thead>
            <tbody>
              {anns.map((a) => (
                <tr key={a.id}>
                  <td><b>{a.pinned && "📌 "}{a.title}</b><div className="small muted">{a.body.slice(0, 120)}</div></td>
                  <td><span className="pill">{KIND_LABEL[a.kind]}</span></td>
                  <td><span className={`pill ${a.published ? "ok" : ""}`}>{a.published ? "published" : "draft"}</span></td>
                  <td className="small muted">{when(a.updated_at)} · {a.author}</td>
                  <td className="small">
                    {canEdit && <>
                      <button className="ghost small" onClick={() => setEditAnn({ id: a.id, title: a.title, body: a.body, kind: a.kind, pinned: a.pinned, published: a.published, link: a.link })}>Edit</button>{" "}
                      <button className="danger small" onClick={async () => { if (confirm("Delete this announcement?") && (await deleteAnnouncement(a.id))) setAnns(anns.filter((x) => x.id !== a.id)); }}>✕</button>
                    </>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section === "roadmap" && (
        <div className="section">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Public roadmap</h2>
            {canEdit && <button className="primary small" onClick={() => setEditRm(BLANK_RM)}>+ New item</button>}
          </div>
          <p className="hint">Shown on /roadmap and in every tenant&apos;s What&apos;s new page, where they can vote.</p>
          {editRm && (
            <form className="form card" onSubmit={saveRm} style={{ marginBottom: "1rem" }}>
              <div className="two">
                <label>Title<input required value={editRm.title} onChange={(e) => setEditRm({ ...editRm, title: e.target.value })} /></label>
                <label>Status<select value={editRm.status} onChange={(e) => setEditRm({ ...editRm, status: e.target.value as RoadmapStatus })}>{(Object.keys(ROADMAP_LABEL) as RoadmapStatus[]).map((k) => <option key={k} value={k}>{ROADMAP_LABEL[k]}</option>)}</select></label>
                <label>Category<input value={editRm.category} onChange={(e) => setEditRm({ ...editRm, category: e.target.value })} placeholder="integrations, telephony…" /></label>
                <label>ETA (free text)<input value={editRm.eta ?? ""} onChange={(e) => setEditRm({ ...editRm, eta: e.target.value || null })} placeholder="Q1 2027" /></label>
              </div>
              <label>Description<textarea rows={3} value={editRm.description} onChange={(e) => setEditRm({ ...editRm, description: e.target.value })} /></label>
              <div className="row"><button className="primary">Save</button><button type="button" className="ghost" onClick={() => setEditRm(null)}>Cancel</button></div>
            </form>
          )}
          <table>
            <thead><tr><th>Item</th><th>Status</th><th>Votes</th><th></th></tr></thead>
            <tbody>
              {roadmap.map((r) => (
                <tr key={r.id}>
                  <td><b>{r.title}</b> {r.category && <span className="pill">{humanize(r.category)}</span>}<div className="small muted">{r.description}</div></td>
                  <td><span className="pill">{ROADMAP_LABEL[r.status]}</span>{r.eta && <div className="small muted">{r.eta}</div>}</td>
                  <td>▲ {r.votes}</td>
                  <td className="small">
                    {canEdit && <>
                      <button className="ghost small" onClick={() => setEditRm({ id: r.id, title: r.title, description: r.description, status: r.status, category: r.category, eta: r.eta })}>Edit</button>{" "}
                      <button className="danger small" onClick={async () => { if (confirm("Delete this roadmap item?") && (await deleteRoadmapItem(r.id))) setRoadmap(roadmap.filter((x) => x.id !== r.id)); }}>✕</button>
                    </>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {section === "feedback" && (
        <div className="section">
          <h2>Customer feedback</h2>
          {feedback.length === 0 && <p className="small muted">No feedback yet.</p>}
          <table>
            <thead><tr><th>When</th><th>Tenant</th><th>Type</th><th>Message</th><th>Status</th></tr></thead>
            <tbody>
              {feedback.map((f) => (
                <tr key={f.id}>
                  <td className="small muted">{when(f.created_at)}</td>
                  <td className="small"><b>{f.tenant_id}</b><br />{f.author}</td>
                  <td><span className={`pill ${f.kind === "bug" ? "bad" : f.kind === "praise" ? "ok" : ""}`}>{humanize(f.kind)}</span></td>
                  <td className="small">{f.text}{f.page && <div className="muted">from {f.page}</div>}</td>
                  <td>
                    <select className="field" disabled={!canEdit} value={f.status} onChange={async (e) => { const r = await setFeedbackStatus(f.id, e.target.value as Feedback["status"]); if (r) setFeedback(feedback.map((x) => (x.id === f.id ? r : x))); }}>
                      {(["new", "reviewed", "planned", "closed"] as const).map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
