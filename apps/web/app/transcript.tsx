export type TranscriptTurn = { role: string; text: string; interrupted?: boolean; at?: string };

function clock(iso: string | undefined, start: string | undefined): string | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const s = start ? new Date(start).getTime() : NaN;
  if (!Number.isNaN(s) && t >= s) {
    const d = Math.round((t - s) / 1000);
    return `${Math.floor(d / 60)}:${String(d % 60).padStart(2, "0")}`;
  }
  return new Date(t).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

/** Chat-style rendering of a call transcript, shared by the call detail page and public share links. */
export function Transcript({
  turns,
  assistantName = "Assistant",
  startedAt,
  emptyText = "No transcript.",
}: {
  turns: TranscriptTurn[];
  assistantName?: string;
  startedAt?: string;
  emptyText?: string;
}) {
  if (!turns.length) return <p className="muted">{emptyText}</p>;
  return (
    <ol className="convo" aria-label="Call transcript">
      {turns.map((t, i) => {
        const assistant = t.role === "assistant";
        const time = clock(t.at, startedAt);
        return (
          <li key={i} className={`turn ${assistant ? "assistant" : "caller"}`}>
            <span className="avatar" aria-hidden>{assistant ? "AI" : "C"}</span>
            <div className="body">
              <div className="meta">
                <span className="who">{assistant ? assistantName : "Caller"}</span>
                {time && <time dateTime={t.at}>{time}</time>}
                {t.interrupted && <span className="pill warn">interrupted</span>}
              </div>
              <p className="text">{t.text}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
