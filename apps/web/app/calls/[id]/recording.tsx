"use client";

import { useEffect, useRef, useState } from "react";
import { fetchRecordingUrl } from "@/lib/api";

function legLabel(key: string): string {
  const file = key.split("/").pop() ?? key;
  if (file.startsWith("caller")) return "Caller";
  if (file.startsWith("agent")) return "Assistant";
  if (file.startsWith("human")) return "Team member";
  return file.replace(/\.[a-z0-9]+$/i, "");
}

/** One recording leg: loads the audio through the API (auth) and offers play + download. */
export function RecordingLeg({ callId, index, objectKey, seekTo = null }: {
  callId: string; index: number; objectKey: string; seekTo?: number | null;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const seeked = useRef(false);

  const jump = () => {
    const el = audio.current;
    if (!el || seekTo == null || seeked.current) return;
    seeked.current = true;
    el.currentTime = Math.max(0, seekTo - 2);
    void el.play().catch(() => undefined);
  };

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    fetchRecordingUrl(callId, index).then((r) => {
      if (!active) return;
      if (r.ok) {
        objectUrl = r.data;
        setUrl(r.data);
      } else setError(r.error);
    });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [callId, index]);

  const ext = objectKey.match(/\.[a-z0-9]+$/i)?.[0] ?? ".ogg";
  return (
    <div className="recording-leg">
      <div className="row between">
        <strong>{legLabel(objectKey)}{seekTo != null && <span className="pill accent" style={{ marginLeft: 8 }}>from {Math.floor(seekTo / 60)}:{String(Math.floor(seekTo % 60)).padStart(2, "0")}</span>}</strong>
        {url && <a href={url} download={`${callId}-${legLabel(objectKey).toLowerCase()}${ext}`} className="small">Download</a>}
      </div>
      {url ? (
        <audio ref={audio} controls preload="metadata" src={url} style={{ width: "100%" }} onLoadedMetadata={jump} />
      ) : error ? (
        <p className="muted small">{error}</p>
      ) : (
        <p className="muted small">Loading…</p>
      )}
    </div>
  );
}
