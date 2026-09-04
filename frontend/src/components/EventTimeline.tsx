import { useState } from "react";
import type { KraftEvent } from "../types";
import { LogModal } from "./LogModal";

/**
 * The one field of an event's payload worth reading at a glance. Without this
 * the timeline shows only a type and a timestamp, so the reason an item stopped
 * needing a human — or what a fix cycle is retrying — was fetched but never
 * shown (Kraft-2ih).
 */
function detailOf(e: KraftEvent): string | null {
  const p = e.payload as Record<string, unknown>;
  if (e.type === "gate_rejected" && typeof p.note === "string") return p.note;
  if (e.type === "work_item_needs_human" && typeof p.reason === "string") return p.reason;
  if (e.type === "fix_cycle_started" && Array.isArray(p.failed_tasks)) {
    return `cycle ${p.cycle}: ${(p.failed_tasks as string[]).join(", ")}`;
  }
  return null;
}

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
            {detailOf(e) && <span className="event-detail">{detailOf(e)}</span>}
          </li>
        ))}
      </ol>
      {sid && <LogModal sessionId={sid} onClose={() => setSid(null)} />}
    </section>
  );
}
