"use client";

import Link from "next/link";
import { useState } from "react";
import {
  type AnnouncementFeed, type FeedbackKind, type RoadmapStatus, type RoadmapView,
  KIND_LABEL, ROADMAP_LABEL, markAnnouncementsRead, submitFeedback, voteRoadmap, when,
} from "@/lib/api";

const ROADMAP_ORDER: RoadmapStatus[] = ["in_progress", "planned", "considering", "shipped"];

export default function WhatsNew({ tenant, feed: initial, roadmap: initialRoadmap }: { tenant: string; feed: AnnouncementFeed; roadmap: RoadmapView[] }) {
  const [feed, setFeed] = useState(initial);
  const [roadmap, setRoadmap] = useState(initialRoadmap);
  const [fb, setFb] = useState<{ kind: FeedbackKind; text: string }>({ kind: "idea", text: "" });
  const [fbState, setFbState] = useState<"idle" | "busy" | "sent" | "error">("idle");

  const markAll = async () => { const f = await markAnnouncementsRead(tenant); if (f) setFeed(f); };
  const vote = async (id: string) => { const r = await voteRoadmap(tenant, id); if (r) setRoadmap(roadmap.map((x) => (x.id === id ? r : x))); };
  const send = async (e: React.FormEvent) => {
    e.preventDefault(); setFbState("busy");
    const r = await submitFeedback(tenant, { ...fb, page: "/whats-new" });
    setFbState(r.ok ? "sent" : "error");
    if (r.ok) setFb({ kind: "idea", text: "" });
  };

  return (
    <>
      <h1>What&apos;s new</h1>
      <p className="hint">Product updates, what we&apos;re building next, and a place to tell us what you need. Public pages: <Link href="/changelog">changelog</Link> · <Link href="/roadmap">roadmap</Link>.</p>

      <div className="section">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>Updates {feed.unread > 0 && <span className="pill warn">{feed.unread} unread</span>}</h2>
          {feed.unread > 0 && <button className="ghost small" onClick={markAll}>Mark all read</button>}
        </div>
        {feed.items.length === 0 && <p className="small muted">No updates yet.</p>}
        {feed.items.map((a) => (
          <div className="card" key={a.id} style={{ marginBottom: ".6rem", borderLeft: a.read ? undefined : "3px solid var(--accent)" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{a.pinned && <span className="pill" style={{ marginRight: ".4rem" }}>Pinned</span>}{a.title}</strong>
              <span className="small muted"><span className="pill">{KIND_LABEL[a.kind] ?? a.kind}</span> {when(a.created_at)}</span>
            </div>
            {a.body.split("\n").filter(Boolean).map((p, i) => <p key={i} className="small" style={{ margin: ".3rem 0" }}>{p}</p>)}
            {a.link && <Link className="small" href={a.link}>Learn more →</Link>}
            {!a.read && <button className="ghost small" style={{ marginLeft: ".5rem" }} onClick={async () => { const f = await markAnnouncementsRead(tenant, a.id); if (f) setFeed(f); }}>Mark read</button>}
          </div>
        ))}
      </div>

      <div className="section">
        <h2>Roadmap</h2>
        <p className="hint">Vote for what matters to you — one vote per organisation per item.</p>
        {roadmap.length === 0 && <p className="small muted">Nothing published yet.</p>}
        {ROADMAP_ORDER.filter((st) => roadmap.some((r) => r.status === st)).map((st) => (
          <div key={st} style={{ marginBottom: ".8rem" }}>
            <h3 className="small">{ROADMAP_LABEL[st]}</h3>
            {roadmap.filter((r) => r.status === st).map((r) => (
              <div className="list-row" key={r.id} style={{ gridTemplateColumns: "1fr auto", alignItems: "center" }}>
                <div>
                  <b className="small">{r.title}</b> {r.category && <span className="pill">{r.category}</span>} {r.eta && <span className="small muted">· {r.eta}</span>}
                  <div className="small muted">{r.description}</div>
                </div>
                <button className={r.voted ? "primary small" : "ghost small"} disabled={r.voted || st === "shipped"} onClick={() => vote(r.id)}>▲ {r.votes}{r.voted ? " · voted" : ""}</button>
              </div>
            ))}
          </div>
        ))}
      </div>

      <div className="section">
        <h2>Send feedback</h2>
        <form className="form" onSubmit={send}>
          <div className="two">
            <label>Type
              <select value={fb.kind} onChange={(e) => setFb({ ...fb, kind: e.target.value as FeedbackKind })}>
                <option value="idea">Idea / request</option><option value="bug">Something&apos;s wrong</option><option value="praise">Praise</option><option value="other">Other</option>
              </select>
            </label>
          </div>
          <label>Message<textarea required rows={4} value={fb.text} onChange={(e) => setFb({ ...fb, text: e.target.value })} placeholder="What would make Parlio more useful for you?" /></label>
          <div className="row">
            <button className="primary" disabled={fbState === "busy" || !fb.text.trim()}>{fbState === "busy" ? "Sending…" : "Send"}</button>
            {fbState === "sent" && <span className="small" style={{ color: "var(--ok-fg)" }}>Thanks — we read every message.</span>}
            {fbState === "error" && <span className="small" style={{ color: "var(--bad-fg)" }}>Couldn&apos;t send, try again.</span>}
          </div>
        </form>
      </div>
    </>
  );
}
