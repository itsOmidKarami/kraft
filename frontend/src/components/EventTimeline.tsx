import { useState } from "react";
import { clock } from "../format";
import type { KraftEvent } from "../types";
import { LogModal } from "./LogModal";

/**
 * The Timeline tab (design 4d): events grouped by the node they happened on,
 * newest node first, newest event first inside each group.
 */

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
  // Concerns reach the UI only through the gate panel, so a chain with no
  // review gate stored them and showed them nowhere; the timeline is the one
  // surface every chain has.
  if (e.type === "worker_session_exited" && typeof p.concerns === "string") return p.concerns;
  // A dead webhook has to be tellable from a quiet one, which is the whole
  // reason `notify` records this event rather than only logging it.
  if (e.type === "notification_failed") {
    const why = p.status ?? p.error ?? "no response";
    return `${p.event_type} → ${p.host} · ${why}`;
  }
  if (e.type === "fix_cycle_started" && Array.isArray(p.failed_tasks)) {
    return `cycle ${p.cycle}: ${(p.failed_tasks as string[]).join(", ")}`;
  }
  return null;
}

interface Group {
  node: string;
  events: KraftEvent[];
  span: string;
}

/**
 * Most events carry a `node_id`; the ones that don't (gate decisions, work-item
 * lifecycle) belong to whichever node was running when they landed, so the node
 * is carried forward rather than dropping those events into a limbo group.
 */
function groupByNode(events: KraftEvent[]): Group[] {
  const order: string[] = [];
  const byNode = new Map<string, KraftEvent[]>();
  let node = "—";
  for (const e of events) {
    const id = (e.payload as Record<string, unknown>).node_id;
    if (typeof id === "string") node = id;
    if (!byNode.has(node)) {
      byNode.set(node, []);
      order.push(node);
    }
    byNode.get(node)!.push(e);
  }
  return order
    .reverse()
    .map((n) => {
      const rows = byNode.get(n)!;
      const from = clock(rows[0].created_at);
      const to = clock(rows[rows.length - 1].created_at);
      return { node: n, events: [...rows].reverse(), span: from === to ? from : `${from} – ${to}` };
    });
}

export function EventTimeline({ events }: { events: KraftEvent[] }) {
  const [sid, setSid] = useState<string | null>(null);
  const groups = groupByNode(events);

  if (groups.length === 0) return <p className="empty">no events yet</p>;

  return (
    <section className="timeline">
      {groups.map((g, gi) => (
        <div key={g.node} className="timeline-group" data-node={g.node}>
          <div className="timeline-node">
            {/* the live node leads in the accent; older groups fade back */}
            <span className="timeline-node-name" data-age={Math.min(gi, 3)}>{g.node}</span>
            <span className="timeline-span">{g.span}</span>
          </div>
          <div>
            {g.events.map((e) => (
              <div key={e.seq} className="event-row" data-type={e.type}>
                <span className="event-dot" data-age={Math.min(gi, 3)} />
                <div className="event-body">
                  <span className="etype">{e.type}</span>
                  {detailOf(e) && <span className="event-detail">{detailOf(e)}</span>}
                  {e.type.startsWith("worker_session_") &&
                    typeof e.payload.session_id === "string" && (
                      <button
                        className="btn btn-ghost event-log"
                        onClick={() => setSid(e.payload.session_id as string)}
                      >
                        view log
                      </button>
                    )}
                </div>
                <time>{clock(e.created_at)}</time>
              </div>
            ))}
          </div>
        </div>
      ))}
      {sid && <LogModal sessionId={sid} onClose={() => setSid(null)} />}
    </section>
  );
}
