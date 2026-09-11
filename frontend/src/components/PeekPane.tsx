import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api";
import { useStore } from "../store";
import { deriveState } from "../deriveState";
import { Gate } from "./Gate";
import { MiniChain } from "./ui";
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

  return (
    <aside className="peek-pane" aria-label="peek">
      <div className="peek-head">
        <code>{item.id}</code>
        <Link to={`/work-items/${item.id}`}>Open →</Link>
      </div>
      <div className="peek-meta">
        <span title={item.repo}>{repoName(item.repo)}</span>
        <span>{item.chain_template}</span>
        <span className="tag tag-outline tag-tight">{state.state}</span>
      </div>
      <h2 className="peek-title">{item.title}</h2>
      <MiniChain
        nodes={item.chain_definition.nodes}
        currentNodeId={item.current_node_id}
        done={item.completedNodes}
        size="lg"
        paused={["paused", "capped", "abandoned", "rate_limited", "waiting"].includes(state.state)}
      />
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
      <p className="peek-updated">updated {ago(item.updated_at)}</p>
      <Link to={`/work-items/${item.id}`} className="btn btn-secondary">
        Open →
      </Link>
    </aside>
  );
}
