import { useState } from "react";
import type { KraftEvent } from "../types";
import { LogModal } from "./LogModal";

export function EventTimeline({ events }: { events: KraftEvent[] }) {
  const [sid, setSid] = useState<string | null>(null);
  return (
    <section className="timeline">
      <ol>
        {[...events].reverse().map((e) => (
          <li key={e.seq} className="event-row" data-type={e.type}>
            <time>{e.created_at}</time>
            <span className="etype">{e.type}</span>
            {e.type.startsWith("worker_session_") && typeof e.payload.session_id === "string" && (
              <button onClick={() => setSid(e.payload.session_id as string)}>view log</button>
            )}
          </li>
        ))}
      </ol>
      {sid && <LogModal sessionId={sid} onClose={() => setSid(null)} />}
    </section>
  );
}
