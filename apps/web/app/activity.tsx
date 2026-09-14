"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchNavBadges, liveSocketUrl, type LiveMessage, type NavBadges } from "@/lib/api";

export const EMPTY_BADGES: NavBadges = { live: 0, inbox: 0, tickets: 0, transfers: 0, outbound: 0, support: 0, total: 0 };
export const BADGE_FOR_HREF: Record<string, keyof NavBadges> = {
  "/live": "live",
  "/inbox": "inbox",
  "/tickets": "tickets",
  "/handoff": "transfers",
  "/outbound": "outbound",
  "/support": "support",
};

const POLL_MS = 20_000;
const REFRESH_TYPES = /^(inbox\.|approval\.|call\.|snapshot)/;

export type Alert = { id: string; title: string; body: string; href: string };

function alertFor(m: LiveMessage): Alert | null {
  if (m.type === "inbox.handoff") {
    const t = m.payload.thread as { id: string; channel: string; contact_name: string | null; identity: string } | undefined;
    const msg = m.payload.message as { text: string } | null | undefined;
    if (!t) return null;
    return {
      id: `handoff-${t.id}-${m.at}`,
      title: `${t.channel === "webchat" ? "Web chat" : t.channel.toUpperCase()} waiting for a human`,
      body: `${t.contact_name || t.identity}: ${msg?.text ?? ""}`.slice(0, 160),
      href: `/inbox?thread=${encodeURIComponent(t.id)}`,
    };
  }
  if (m.type === "approval.requested") {
    const a = m.payload as { id?: string; kind?: string; details?: string };
    return { id: `approval-${a.id ?? m.at}`, title: "Caller needs your approval", body: (a.details || a.kind || "").slice(0, 160), href: "/live" };
  }
  return null;
}

function chime() {
  try {
    const ctx = new AudioContext();
    const play = (freq: number, at: number) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, at);
      g.gain.exponentialRampToValueAtTime(0.25, at + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, at + 0.45);
      o.connect(g).connect(ctx.destination);
      o.start(at);
      o.stop(at + 0.5);
    };
    play(880, ctx.currentTime);
    play(1320, ctx.currentTime + 0.18);
    setTimeout(() => void ctx.close(), 1200);
  } catch {}
}

function desktopNotify(a: Alert) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  if (document.visibilityState === "visible" && document.hasFocus()) return;
  try {
    const n = new Notification(a.title, { body: a.body, icon: "/logo-icon.png", tag: a.id });
    n.onclick = () => { window.focus(); window.location.assign(a.href); n.close(); };
  } catch {}
}

export function useActivity(tenantId: string | null) {
  const [badges, setBadges] = useState<NavBadges>(EMPTY_BADGES);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const inflight = useRef(false);
  const baseTitle = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    if (!tenantId || inflight.current) return;
    inflight.current = true;
    const b = await fetchNavBadges(tenantId);
    inflight.current = false;
    if (b) setBadges(b);
  }, [tenantId]);

  useEffect(() => {
    if (!tenantId) return;
    void refresh();
    const t = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onVisible); };
  }, [tenantId, refresh]);

  useEffect(() => {
    if (!tenantId) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let retry = 2000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let debounce: ReturnType<typeof setTimeout> | undefined;
    const connect = async () => {
      ws = new WebSocket(await liveSocketUrl(tenantId));
      ws.onopen = () => { retry = 2000; };
      ws.onmessage = (e) => {
        const m = JSON.parse(String(e.data)) as LiveMessage;
        if (REFRESH_TYPES.test(m.type)) {
          clearTimeout(debounce);
          debounce = setTimeout(() => void refresh(), 400);
        }
        const a = alertFor(m);
        if (a) {
          setAlerts((prev) => (prev.some((x) => x.id === a.id) ? prev : [...prev, a]));
          chime();
          desktopNotify(a);
        }
      };
      ws.onclose = () => {
        if (!closed) { timer = setTimeout(connect, retry); retry = Math.min(retry * 2, 30000); }
      };
      ws.onerror = () => ws?.close();
    };
    void connect();
    return () => { closed = true; clearTimeout(timer); clearTimeout(debounce); ws?.close(); };
  }, [tenantId, refresh]);

  // Tab title: "(3) Parlio", flashing while an alert is unacknowledged.
  useEffect(() => {
    baseTitle.current ??= document.title.replace(/^\(\d+\) /, "");
    const base = baseTitle.current;
    const count = badges.total;
    if (!alerts.length) {
      document.title = count ? `(${count}) ${base}` : base;
      return;
    }
    let on = false;
    const tick = () => { on = !on; document.title = on ? `\u25CF ${alerts[0].title}` : `(${count || alerts.length}) ${base}`; };
    tick();
    const t = setInterval(tick, 1200);
    return () => { clearInterval(t); document.title = count ? `(${count}) ${base}` : base; };
  }, [alerts, badges.total]);

  const dismiss = useCallback((id: string) => setAlerts((prev) => prev.filter((a) => a.id !== id)), []);
  return { badges, alerts, dismiss };
}

export function ActivityAlerts({ alerts, dismiss }: { alerts: Alert[]; dismiss: (id: string) => void }) {
  const [perm, setPerm] = useState<NotificationPermission | "unsupported">("unsupported");
  useEffect(() => { setPerm(typeof Notification === "undefined" ? "unsupported" : Notification.permission); }, []);
  if (!alerts.length) return null;
  return (
    <div className="activity-toasts" role="status" aria-live="assertive">
      {alerts.slice(0, 3).map((a) => (
        <div key={a.id} className="activity-toast">
          <div className="activity-toast-body">
            <strong>{a.title}</strong>
            {a.body && <div className="muted">{a.body}</div>}
          </div>
          <div className="activity-toast-actions">
            <Link href={a.href} className="btn" onClick={() => dismiss(a.id)}>Open</Link>
            <button type="button" className="ghost" onClick={() => dismiss(a.id)}>Dismiss</button>
          </div>
          {perm === "default" && (
            <button type="button" className="activity-toast-perm" onClick={() => void Notification.requestPermission().then(setPerm)}>
              Enable desktop notifications
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
