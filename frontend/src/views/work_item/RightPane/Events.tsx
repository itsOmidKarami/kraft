import { useEffect, useRef, useState } from "react";
import { clock } from "../../../format";
import type { KraftEvent, WorkerSession } from "../../../types";
import { detailOf, findingsOf, groupByNode, titleOf } from "../timelineHelpers";

/**
 * Right pane · Timeline (UI v2 · 05, 15): the event list for the node
 * `Inspector/Timeline.tsx` selected, with an `all | gates` chip. `onViewLog`
 * routes "view log" to the Tasks tab/Log pane instead of opening `LogModal`.
 */

const GATE_TYPES = new Set(["gate_requested", "gate_approved", "gate_rejected", "chain_spliced"]);

export function Events({
  events,
  sessions = [],
  nodeId,
  selectedSeq = null,
  onViewLog,
}: {
  events: KraftEvent[];
  sessions?: WorkerSession[];
  nodeId: string | null;
  /** The event the Timeline list picked (W11 · F): marked, and brought into view. */
  selectedSeq?: number | null;
  onViewLog: (sessionId: string) => void;
}) {
  const [filter, setFilter] = useState<"all" | "gates" | "tasks">("all");
  const selectedRow = useRef<HTMLDivElement>(null);
  useEffect(() => {
    selectedRow.current?.scrollIntoView?.({ block: "nearest" });
  }, [nodeId, selectedSeq]);
  const hooks = new Map(sessions.map((s) => [s.id, s.hook_point]));
  const group = groupByNode(events).find((g) => g.node === nodeId);
  const rows = (group?.events ?? []).filter(
    (e) => filter === "all" || (filter === "gates" ? GATE_TYPES.has(e.type) : e.type === "task_progress"),
  );

  return (
    <div className="pane events-pane" data-testid="right-pane-events">
      <header className="diff-modal-head">
        <span className="mono">{nodeId ?? "—"}</span>
        <span className="diff-totals">
          {group?.events.length ?? 0} events{group && ` · ${group.span}`}
        </span>
        <div className="log-filters">
          <button className="log-chip" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>
            all
          </button>
          <button className="log-chip" aria-pressed={filter === "gates"} onClick={() => setFilter("gates")}>
            gates
          </button>
          <button className="log-chip" aria-pressed={filter === "tasks"} onClick={() => setFilter("tasks")}>
            tasks
          </button>
        </div>
      </header>
      {rows.length === 0 && <p className="empty">no {filter === "gates" ? "gate " : ""}events on this node</p>}
      <div className="timeline">
        {rows.map((e) => (
          <div
            key={e.seq}
            ref={e.seq === selectedSeq ? selectedRow : undefined}
            className="event-row"
            data-type={e.type}
            data-selected={e.seq === selectedSeq || undefined}
          >
            <span className="event-dot" />
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
              {/* Kraft-a4js: the card that links here only ever gives a count
                  ("3 findings deferred"). This is where the messages, files
                  and severities themselves become readable -- the Events
                  pane already scrolls (screen 48), unlike the gate card. */}
              {findingsOf(e).length > 0 && (
                <ul className="event-findings">
                  {findingsOf(e).map((f, i) => (
                    <li key={`${f.source_plugin}:${f.file}:${i}`}>
                      <span className="field-hint">{f.severity}</span>{" "}
                      <span className="mono">{f.file ? `${f.file}:${f.line ?? "?"}` : "—"}</span>{" "}
                      {f.message}
                    </li>
                  ))}
                </ul>
              )}
              {typeof e.payload.session_id === "string" && (
                <button className="btn btn-ghost event-log" onClick={() => onViewLog(e.payload.session_id as string)}>
                  view log
                </button>
              )}
            </div>
            <time>{clock(e.created_at)}</time>
          </div>
        ))}
      </div>
    </div>
  );
}
