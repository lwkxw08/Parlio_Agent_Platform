"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import {
  type GuidePage,
  type GuidePageSummary,
  type HelpAnswer,
  type HelpCitation,
  askHelp,
  fetchHelpForRoute,
  fetchHelpPage,
  fetchHelpPages,
} from "@/lib/api";

/** Screens that are not part of the signed-in dashboard (public widgets, login). */
const HIDDEN = ["/chat/", "/login", "/share/", "/status", "/approve/", "/onboarding", "/trust"];
export const HELP_EVENT = "parlio:help";

/** Open the help drawer from anywhere (optionally with a question pre-filled). */
export function openHelp(question?: string) {
  window.dispatchEvent(new CustomEvent(HELP_EVENT, { detail: { question } }));
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

/** Find the on-screen section for a guide anchor: an element with that id, else a heading whose text slugs to it. */
function findAnchor(anchor: string): HTMLElement | null {
  const byId = document.getElementById(anchor);
  const el =
    byId ?? Array.from(document.querySelectorAll<HTMLElement>("main h2, main h3, main summary, main legend")).find((h) => slug(h.textContent ?? "") === anchor) ?? null;
  return el ? (el.closest<HTMLElement>(".section, .card, details, fieldset") ?? el) : null;
}

/** Scroll to a guide anchor and flash it; retries briefly while the page is still rendering. */
function scrollToAnchor(anchor: string, tries = 20) {
  const box = findAnchor(anchor);
  if (box) {
    if (box instanceof HTMLDetailsElement) box.open = true;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    box.classList.add("help-flash");
    window.setTimeout(() => box.classList.remove("help-flash"), 2600);
  } else if (tries > 0) {
    window.setTimeout(() => scrollToAnchor(anchor, tries - 1), 150);
  }
}

function useAnchorScroll(path: string) {
  useEffect(() => {
    const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
    if (hash) scrollToAnchor(hash);
  }, [path]);
}

/**
 * For tabbed screens: switch to the tab that owns the guide anchor in the URL hash (on load and
 * when a help citation changes it) so the anchored section is actually on screen to scroll to.
 */
export function useHashTab<T extends string>(anchorTabs: Record<string, T>, setTab: (t: T) => void) {
  useEffect(() => {
    const apply = () => {
      const hash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
      const t = anchorTabs[hash];
      if (t) {
        setTab(t);
        scrollToAnchor(hash);
      }
    };
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

type Turn = { role: "user" | "assistant"; content: string; citations?: HelpCitation[]; source?: HelpAnswer["source"] };

const SUGGESTIONS = [
  "Can I block anonymous callers?",
  "How do I get a text after every call?",
  "How do I send appointment reminders?",
  "How do I transfer calls to my mobile?",
  "How do I change the voice?",
  "How do I add a colleague?",
];

export default function HelpDrawer() {
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"page" | "ask" | "all">("page");
  const [page, setPage] = useState<GuidePage | null>(null);
  const [pages, setPages] = useState<GuidePageSummary[] | null>(null);
  const [browse, setBrowse] = useState<GuidePage | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useAnchorScroll(path);

  const hidden = HIDDEN.some((p) => path.startsWith(p));

  useEffect(() => {
    if (hidden) return;
    let live = true;
    fetchHelpForRoute(path).then((p) => live && setPage(p));
    setBrowse(null);
    return () => {
      live = false;
    };
  }, [path, hidden]);

  useEffect(() => {
    if (!open || tab !== "all" || pages) return;
    fetchHelpPages().then((p) => setPages(p ?? []));
  }, [open, tab, pages]);

  useEffect(() => {
    const onEvt = (e: Event) => {
      const detail = (e as CustomEvent<{ question?: string }>).detail;
      setOpen(true);
      setTab("ask");
      if (detail?.question) setQ(detail.question);
      window.setTimeout(() => inputRef.current?.focus(), 50);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
      if (e.key === "?" && !e.ctrlKey && !e.metaKey) {
        const t = e.target as HTMLElement | null;
        if (t && ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return;
        if (t?.isContentEditable) return;
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener(HELP_EVENT, onEvt);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener(HELP_EVENT, onEvt);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  const ask = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || busy) return;
      setQ("");
      setTab("ask");
      const history = turns.slice(-6).map((t) => ({ role: t.role, content: t.content }));
      setTurns((ts) => [...ts, { role: "user", content: text }]);
      setBusy(true);
      const a = await askHelp({ question: text, route: path, history });
      setBusy(false);
      setTurns((ts) => [
        ...ts,
        a
          ? { role: "assistant", content: a.answer, citations: a.citations, source: a.source }
          : { role: "assistant", content: "Sorry — I couldn't reach the help service just now. Please try again in a moment.", source: "none" },
      ]);
    },
    [busy, path, turns],
  );

  const go = (c: HelpCitation) => {
    setOpen(false);
    const href = `${c.route}#${c.anchor}`;
    if (c.route === path) {
      window.history.replaceState(null, "", href);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
      scrollToAnchor(c.anchor);
    } else {
      router.push(href);
    }
  };

  if (hidden) return null;

  return (
    <>
      {!open && (
        <button type="button" className="help-fab" onClick={() => setOpen(true)} aria-label="Help and Ask Parlio" title="Help (press ?)">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 015 0c0 1.6-2.5 2-2.5 3.5M12 17h.01" /></svg>
        </button>
      )}
      {open && <div className="help-scrim" onClick={() => setOpen(false)} aria-hidden />}
      <aside className={`help-drawer${open ? " open" : ""}`} aria-label="Help" aria-hidden={!open}>
        <header className="help-head">
          <div>
            <strong>Help</strong>
            <span className="muted small" style={{ marginLeft: 8 }}>Guide &amp; Ask Parlio</span>
          </div>
          <button type="button" className="ghost" onClick={() => setOpen(false)} aria-label="Close help">Close</button>
        </header>
        <nav className="tabs help-tabs">
          <button type="button" className={tab === "page" ? "active" : ""} onClick={() => setTab("page")}>This page</button>
          <button type="button" className={tab === "ask" ? "active" : ""} onClick={() => setTab("ask")}>Ask Parlio</button>
          <button type="button" className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>All guides</button>
        </nav>

        {tab === "page" && (
          <div className="help-body">
            {page ? (
              <PageGuide page={page} onAsk={(question) => void ask(question)} />
            ) : (
              <div className="help-empty">
                <p className="muted">There's no guide page for this screen yet.</p>
                <button type="button" className="btn" onClick={() => setTab("ask")}>Ask Parlio instead</button>
              </div>
            )}
          </div>
        )}

        {tab === "all" && (
          <div className="help-body">
            {browse ? (
              <>
                <button type="button" className="ghost small" onClick={() => setBrowse(null)}>← All guides</button>
                <PageGuide page={browse} onAsk={(question) => void ask(question)} />
              </>
            ) : pages == null ? (
              <p className="muted small">Loading…</p>
            ) : (
              <ul className="help-list">
                {pages.map((p) => (
                  <li key={p.slug}>
                    <button
                      type="button"
                      onClick={() => {
                        setBrowse(null);
                        fetchHelpPage(p.slug).then((full) => full && setBrowse(full));
                      }}
                    >
                      <strong>{p.title}</strong>
                      <span className="muted small">{p.summary}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {tab === "ask" && (
          <>
            <div className="help-body help-log" ref={logRef}>
              {turns.length === 0 && (
                <div className="help-empty">
                  <p className="muted small">Ask anything about setting up or using Parlio — answers come from the guide and point you to the exact setting.</p>
                  <div className="help-suggest">
                    {SUGGESTIONS.map((s) => (
                      <button key={s} type="button" onClick={() => void ask(s)}>{s}</button>
                    ))}
                  </div>
                </div>
              )}
              {turns.map((t, i) => (
                <div key={i} className={`help-turn ${t.role}`}>
                  <div className="help-bubble">
                    <Md text={t.content} />
                    {t.citations && t.citations.length > 0 && (
                      <div className="help-cites">
                        {t.citations.map((c) => (
                          <button key={`${c.page}#${c.anchor}`} type="button" onClick={() => go(c)} title={`Open ${c.route}`}>
                            <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M7 17L17 7M8 7h9v9" /></svg>
                            {c.path}
                          </button>
                        ))}
                      </div>
                    )}
                    {t.role === "assistant" && t.source === "guide" && <div className="muted small" style={{ marginTop: 6 }}>From the guide</div>}
                  </div>
                </div>
              ))}
              {busy && (
                <div className="help-turn assistant">
                  <div className="help-bubble muted">Thinking…</div>
                </div>
              )}
            </div>
            <form
              className="help-ask"
              onSubmit={(e) => {
                e.preventDefault();
                void ask(q);
              }}
            >
              <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. can I block anonymous callers?" maxLength={600} />
              <button type="submit" className="primary" disabled={busy || !q.trim()}>Ask</button>
            </form>
          </>
        )}
      </aside>
    </>
  );
}

function PageGuide({ page, onAsk }: { page: GuidePage; onAsk: (q: string) => void }) {
  const [openAll, setOpenAll] = useState(false);
  const intro = page.sections.find((s) => s.heading === page.title);
  const rest = page.sections.filter((s) => s.heading !== page.title);
  return (
    <div className="help-page">
      <h3>{page.title}</h3>
      {page.summary && <p className="muted small">{page.summary}</p>}
      {intro && <Md text={intro.body} />}
      {rest.length > 0 && (
        <div className="row" style={{ justifyContent: "space-between", margin: "0.6rem 0 0.3rem" }}>
          <span className="small muted">{rest.length} settings &amp; sections</span>
          <button type="button" className="ghost small" onClick={() => setOpenAll((v) => !v)}>{openAll ? "Collapse all" : "Expand all"}</button>
        </div>
      )}
      {rest.map((s) => (
        <details key={s.anchor} className="help-section" open={openAll || undefined}>
          <summary>{s.heading}</summary>
          <Md text={s.body} />
          {s.route !== "/support" && (
            <Link href={`${s.route}#${s.anchor}`} className="small help-jump">Show me this setting →</Link>
          )}
        </details>
      ))}
      <p className="small muted" style={{ marginTop: "0.8rem" }}>
        Not what you were after?{" "}
        <button type="button" className="link" onClick={() => onAsk(`How do I use ${page.title}?`)}>Ask Parlio</button>
      </p>
    </div>
  );
}

/** Tiny markdown renderer for guide text: paragraphs, bullets, numbered lists, tables, **bold**, *italic*, `code`, [n] refs. */
function Md({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (/^\s*[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*•]\s+/, ""));
      blocks.push(<ul key={key++}>{items.map((it, j) => <li key={j}>{inline(it)}</li>)}</ul>);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ""));
      blocks.push(<ol key={key++}>{items.map((it, j) => <li key={j}>{inline(it)}</li>)}</ol>);
      continue;
    }
    if (/^#{1,6}\s/.test(line)) {
      blocks.push(<h4 key={key++}>{inline(line.replace(/^#+\s*/, ""))}</h4>);
      i++;
      continue;
    }
    if (line.startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i].split("|").slice(1, -1).map((c) => c.trim());
        if (!cells.every((c) => /^:?-{2,}:?$/.test(c))) rows.push(cells);
        i++;
      }
      blocks.push(
        <table key={key++} className="help-table">
          <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => (ri === 0 ? <th key={ci}>{inline(c)}</th> : <td key={ci}>{inline(c)}</td>))}</tr>)}</tbody>
        </table>,
      );
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i]) && !/^#{1,6}\s/.test(lines[i]) && !lines[i].startsWith("|")) para.push(lines[i++].trim());
    blocks.push(<p key={key++}>{inline(para.join(" "))}</p>);
  }
  return <div className="help-md">{blocks}</div>;
}

function inline(s: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[\d+\](?:\[\d+\])*)/g;
  let last = 0;
  let k = 0;
  for (const m of s.matchAll(re)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(s.slice(last, idx));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={k++}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("*")) out.push(<em key={k++}>{tok.slice(1, -1)}</em>);
    else if (tok.startsWith("`")) out.push(<code key={k++}>{tok.slice(1, -1)}</code>);
    else out.push(<sup key={k++} className="muted">{tok}</sup>);
    last = idx + tok.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}
