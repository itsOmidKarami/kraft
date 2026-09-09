import { useState } from "react";
import { clock, elapsed } from "../format";
import type { KraftEvent, WorkerSession } from "../types";
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
  if (e.type === "work_item_rate_limited" && typeof p.retry_at === "string") {
    return `retries at ${p.retry_at}`;
  }
  // A done and a failed exit looked identical, and the 6ms noop-handler exit
  // was invisible for what it is (Kraft-zxu4). Concerns keep their place at
  // the end — they are the longest part of the line.
  if (e.type === "worker_session_exited") {
    const bits: string[] = [];
    if (typeof p.status === "string") bits.push(p.status);
    if (typeof p.wall_ms === "number") bits.push(elapsed(p.wall_ms));
    if (typeof p.concerns === "string") bits.push(p.concerns);
    return bits.length > 0 ? bits.join(" · ") : null;
  }
  if (e.type === "findings_measured" && Array.isArray(p.findings)) {
    const n = (p.findings as unknown[]).length;
    return n > 0 ? `${n} findings` : "no findings";
  }
  // A dead webhook has to be tellable from a quiet one, which is the whole
  // reason `notify` records this event rather than only logging it.
  if (e.type === "notification_failed") {
    const why = p.status ?? p.error ?? "no response";
    return `${p.event_type} → ${p.host} · ${why}`;
  }
  if (e.type === "fix_cycle_started" && Array.isArray(p.failed_tasks)) {
    return `cycle ${p.cycle}: ${(p.failed_tasks as string[]).join(", ")}`;
  }
  // A node that repairs itself runs its own tasks twice, so without this the
  // timeline shows the same measurement happening again and no reason for it
  // (Kraft-rv6i).
  if (e.type === "node_recovery_started" && Array.isArray(p.tasks)) {
    const failed = Array.isArray(p.failed_tasks) ? (p.failed_tasks as string[]).join(", ") : "";
    return `${failed} failed → ${(p.tasks as string[]).join(", ")}`;
  }
  return null;
}

/**
 * The three worker-session events, in words. Only these three: a label table
 * for all ~20 event types goes stale the first time an event is added, and the
 * real problem is that the payload was never rendered (Kraft-zxu4).
 */
const VERBS: Record<string, string> = {
  worker_session_created: "created",
  worker_session_started: "started",
  worker_session_exited: "exited",
};

/**
 * "<hook_point> <verb>", or null when the row cannot name itself.
 *
 * `worker_session_exited` carries only session_id/status/wall_ms, so its hook
 * point comes from the sessions the detail payload already holds. A miss (a
 * session pruned from the payload) returns null and the row keeps the raw
 * type — the behaviour today, never a blank title.
 */
function titleOf(e: KraftEvent, hooks: Map<string, string>): string | null {
  const verb = VERBS[e.type];
  if (!verb) return null;
  const p = e.payload as Record<string, unknown>;
  const hook =
    typeof p.hook_point === "string"
      ? p.hook_point
      : typeof p.session_id === "string"
        ? hooks.get(p.session_id)
        : undefined;
  return hook ? `${hook} ${verb}` : null;
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

export function EventTimeline({
  events,
  sessions = [],
}: {
  events: KraftEvent[];
  /** For resolving an exit row's hook point. Optional so a caller with no
   *  sessions in hand still renders the timeline it renders today. */
  sessions?: WorkerSession[];
}) {
  const [sid, setSid] = useState<string | null>(null);
  const groups = groupByNode(events);
  const hooks = new Map(sessions.map((s) => [s.id, s.hook_point]));

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
                  {(() => {
                    const title = titleOf(e, hooks);
                    return title ? (
                      <>
                        <span className="event-title">{title}</span>
                        <span className="etype">{e.type}</span>
                      </>
                    ) : (
                      <span className="etype">{e.type}</span>
                    );
                  })()}
                  {detailOf(e) && <span className="event-detail">{detailOf(e)}</span>}
                  {/* any event that names a session, not just worker_session_*:
                      work_item_needs_human is the one a human lands on, and its
                      reason names the hook rather than the failure (Kraft-eh6p) */}
                  {typeof e.payload.session_id === "string" && (
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
