import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { X } from "@phosphor-icons/react";
import * as api from "../api";
import { useStore } from "../store";
import { deriveState } from "../deriveState";
import { Gate } from "./Gate";
import { StatusGlyph, TaskBar, TaskLine } from "./ui";
import { BudgetCard } from "./BudgetCard";
import { CappedCard } from "./CappedCard";
import { PausedCard } from "./PausedCard";
import { ago, clock, repoName } from "../format";
import type { LogLine } from "../types";

/**
 * The board's peek pane (UI v2 · 05): the row's own detail, without leaving
 * the board. Same data the detail screen shows for the current node --
 * `hydrateItem` (detail endpoint) plus the last few log lines of the current
 * session -- no new endpoint.
 *
 * A `needs_context` stop (an agent's question) has no reusable card outside
 * `WorkItemDetail.tsx`, which this group does not own -- Open → is the answer
 * path for that one state until that's split out.
 */
export function PeekPane({ id, onClose }: { id: string; onClose: () => void }) {
  const item = useStore((s) => s.workItems[id]);
  const sessions = useStore((s) => s.sessionsByItem[id] ?? []);
  const events = useStore((s) => s.eventsByItem[id] ?? []);

  useEffect(() => {
    useStore.getState().hydrateItem(id).catch(() => {});
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const currentSession = [...sessions].reverse().find((s) => s.node_id === item?.current_node_id);
  const [lines, setLines] = useState<LogLine[]>([]);
  useEffect(() => {
    if (!currentSession?.id) {
      setLines([]);
      return;
    }
    api
      .getLogLines(currentSession.id)
      .then((r) => setLines(r.lines.slice(-4)))
      .catch(() => setLines([]));
  }, [currentSession?.id]);

  if (!item) return null;
  const state = deriveState(item);
  const gate = item.status === "needs_human" ? item.pending_gate : null;

  const nodes = item.chain_definition.nodes;
  const rawIndex = item.current_node_id ? nodes.findIndex((n) => n.id === item.current_node_id) : -1;
  const nodeIndex = rawIndex === -1 ? nodes.length - 1 : rawIndex;
  const doneIds = new Set(item.completedNodes ?? []);
  /** Screen 37 compresses eleven stage rows into three: what is finished,
   *  what is running, and what is left. A group with no nodes is dropped. */
  const stageGroups = [
    {
      kind: "done",
      glyph: "done" as const,
      ids: nodes.filter((n) => doneIds.has(n.id)).map((n) => n.id),
      right: "done",
    },
    {
      kind: "current",
      glyph: state.state,
      ids: nodes.filter((n) => n.id === item.current_node_id).map((n) => n.id),
      right: item.progress ? `${item.progress.current}/${item.progress.total}` : "",
    },
    {
      kind: "remaining",
      glyph: "pending" as const,
      ids: nodes.filter((n) => !doneIds.has(n.id) && n.id !== item.current_node_id).map((n) => n.id),
      right: "",
    },
  ].filter((g) => g.ids.length > 0);
  const remaining = stageGroups.find((g) => g.kind === "remaining");
  if (remaining) remaining.right = `${remaining.ids.length} left`;

  return (
    <>
      {/* Under 1280 the pane floats over the list (UI v3 · 46); CSS hides
          this above that width, where the pane sits beside the rows. */}
      <div className="peek-scrim" onClick={onClose} aria-hidden />
      <aside className="peek-pane" aria-label="peek">
        <div className="peek-head">
          <code>{item.id}</code>
          <span title={item.repo}>{repoName(item.repo)}</span>
          <span>{item.chain_template}</span>
          <span className="tag tag-outline tag-tight">{state.state}</span>
          <Link to={`/work-items/${item.id}`} className="peek-open">
            Open →
          </Link>
          <button className="btn btn-ghost" title="Close (Esc)" aria-label="Close" onClick={onClose}>
            <X size={12} />
          </button>
        </div>
        <h2 className="peek-title">{item.title}</h2>
        <div className="peek-hero">
          <div className="peek-hero-head">
            <span className="peek-hero-node">{item.current_node_id ?? nodes[nodeIndex]?.id}</span>
            <span className="peek-hero-meta">
              node {nodeIndex + 1} of {nodes.length} · {state.state} {ago(item.updated_at)}
            </span>
          </div>
          {item.progress && (
            <>
              <TaskLine progress={item.progress} />
              <TaskBar progress={item.progress} />
            </>
          )}
        </div>
        <div className="peek-stages">
          {stageGroups.map((g) => (
            <div key={g.kind} className="peek-stage" data-kind={g.kind}>
              <StatusGlyph status={g.glyph} />
              <span className="peek-stage-ids">{g.ids.join(" · ")}</span>
              <span className="peek-stage-right">{g.right}</span>
            </div>
          ))}
        </div>
        {gate ? (
          <Gate item={item} gate={gate} sessions={sessions} navigateReject />
        ) : item.budget ? (
          <BudgetCard item={item} sessions={sessions} />
        ) : item.cappedOut ? (
          <CappedCard item={item} sessions={sessions} events={events} />
        ) : item.status === "paused" ? (
          <PausedCard item={item} sessions={sessions} />
        ) : state.needsYou ? (
          <div className="card attention-card">
            <p>Needs a decision this pane cannot make yet.</p>
            <Link className="btn btn-secondary" to={`/work-items/${item.id}`}>
              Open →
            </Link>
          </div>
        ) : null}
        {lines.length > 0 && (
          <div className="peek-log">
            {lines.map((l) => (
              <div key={l.n} className="peek-log-line">
                <span className="peek-log-time">{l.t ? clock(l.t) : ""}</span>
                {l.summary ?? l.text}
              </div>
            ))}
          </div>
        )}
      </aside>
    </>
  );
}
