"use client";

import { useEffect, useRef, useState } from "react";
import type { RemoteTrack } from "livekit-client";
import type { JoinInfo } from "@/lib/api";

export type RoomState = "idle" | "connecting" | "connected" | "error";

/**
 * Joins the call's LiveKit room in the browser: plays every remote audio track (caller + assistant)
 * and, when the supervisor has taken over, publishes the microphone. The server issues a
 * subscribe-only token for listening and a publish-capable one for takeover, so the mode flip
 * simply reconnects with the new token.
 */
export function useRoomAudio(join: JoinInfo | null): { state: RoomState; error: string | null } {
  const [state, setState] = useState<RoomState>("idle");
  const [error, setError] = useState<string | null>(null);
  const audioEls = useRef<HTMLMediaElement[]>([]);

  useEffect(() => {
    if (!join) { setState("idle"); setError(null); return; }
    if (!join.url) {
      setState("connected");
      setError("simulated room (no LiveKit configured) — controls work, no audio");
      return;
    }
    let cancelled = false;
    let disconnect: (() => Promise<void>) | null = null;
    setState("connecting"); setError(null);

    (async () => {
      const { Room, RoomEvent, Track } = await import("livekit-client");
      const room = new Room({ adaptiveStream: false, dynacast: false });
      const attach = (track: RemoteTrack) => {
        if (track.kind !== Track.Kind.Audio) return;
        const el = track.attach();
        el.autoplay = true;
        document.body.appendChild(el);
        audioEls.current.push(el);
      };
      room.on(RoomEvent.TrackSubscribed, (track) => attach(track));
      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        for (const el of track.detach()) { el.remove(); audioEls.current = audioEls.current.filter((x) => x !== el); }
      });
      room.on(RoomEvent.Disconnected, () => { if (!cancelled) setState("idle"); });
      disconnect = async () => { await room.disconnect(); };
      try {
        await room.connect(join.url as string, join.token);
        for (const p of room.remoteParticipants.values()) {
          for (const pub of p.trackPublications.values()) if (pub.track) attach(pub.track);
        }
        await room.startAudio();
        if (join.mode === "taken_over") await room.localParticipant.setMicrophoneEnabled(true);
        if (!cancelled) setState("connected");
      } catch (e) {
        if (!cancelled) { setState("error"); setError(e instanceof Error ? e.message : "could not join room"); }
      }
    })();

    return () => {
      cancelled = true;
      for (const el of audioEls.current) el.remove();
      audioEls.current = [];
      void disconnect?.();
    };
  }, [join]);

  return { state, error };
}
