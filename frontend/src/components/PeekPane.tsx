import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowSquareOut, X } from "@phosphor-icons/react";
import * as api from "../api";
import { useStore } from "../store";
import { deriveState } from "../deriveState";
import { Gate } from "./Gate";
import { StatusGlyph, TaskBar, TaskLine } from "./ui";
import { BudgetCard } from "./BudgetCard";
import { CappedCard } from "./CappedCard";
import { PausedCard } from "./PausedCard";
import { ago, clock, logLineText, repoName } from "../format";
import type { LogLine } from "../types";
import {
  EscalatedCard,
  EscalatingPill,
  dismissedTurnId,
} from "../views/work_item/ActionBar/EscalationCard";
import { useActionBar } from "../views/work_item/ActionBar/useActionBar";

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

  // Clicking away closes it. The scrim (46) does this job under 1280, but
  // above that width there is no scrim -- the pane sits beside rows that must
  // stay clickable -- so the same gesture needs a listener instead. A board
  // row is not "outside": clicking another row switches the peek to it, which
  // is `onSelect`'s job, not this one's.
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const target = e.target as Element | null;
      if (target?.closest?.(".peek-pane, .board-row")) return;
      onClose();
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [onClose]);

  // A node runs more than one hook (implementation runs on.repos.scan too),
  // so "last in the array" hands the pane a job that finished in 0s while the
  // real work is still running. Running first, then newest -- the same rule
  // `views/work_item/index.tsx` already uses for the Tasks tab.
  const nodeSessions = sessions.filter((s) => s.node_id === item?.current_node_id);
  const currentSession =
    nodeSessions.find((s) => s.status === "running") ??
    [...nodeSessions].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
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
  const [dismissed, setDismissed] = useState(() => dismissedTurnId(id));
  const { busy, err, run } = useActionBar(id);
  const navigate = useNavigate();

  if (!item) return null;
  // Same events/sessions the header tag and the card must agree on -- the
  // card used to test raw fields (item.cappedOut etc.) instead of this, so a
  // stranded needs_human stop could show "capped" in the header and a card
  // that refused to act (Kraft-av3t).
  const rawState = deriveState(item, sessions, events);
  const gate = item.status === "needs_human" ? item.pending_gate : null;
  const escalationTurns = sessions
    .filter((s) => s.hook_point === "escalation")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const latestTurn = escalationTurns.at(-1);
  // Same idiom as the item page's ActionBar: dismissing an escalated card is
  // client-side-only state, so re-derive without that escalation session
  // rather than leaving the header stuck on a card the user already closed.
  const state =
    rawState.state === "escalated" && latestTurn && dismissed === latestTurn.id
      ? deriveState(item, [], events)
      : rawState;

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
          {/* Kraft-absw: one click from the board to the artefact a
              human_review gate is about -- render-only, mr_ref is already
              on the item hydrateItem fetches. */}
          {item.mr_ref && (
            <a
              className="mr-link"
              href={item.mr_ref.url}
              target="_blank"
              rel="noreferrer"
            >
              <ArrowSquareOut size={12} />
              MR !{item.mr_ref.number}
            </a>
          )}
          {/* Kraft-3e16 (spec §2.2): `onClose` (Board's `setPeek(null)`,
              written with `replace: true`) clears the board's own ?peek
              before Link's push runs — Link fires this handler first, then
              navigates unless it was prevented. So the entry Back returns
              to is a clean board, not one with the peek still open. */}
          <Link to={`/work-items/${item.id}`} className="peek-open" onClick={onClose}>
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
        {/* Selected from `state.state`, the same derivation the header tag
            above reads -- so the two can never disagree (Kraft-av3t). */}
        {state.state === "gate" && gate ? (
          <Gate item={item} gate={gate} sessions={sessions} navigateReject />
        ) : state.state === "budget" ? (
          <BudgetCard item={item} sessions={sessions} />
        ) : state.state === "capped" ? (
          <CappedCard item={item} sessions={sessions} events={events} />
        ) : state.state === "paused" || state.state === "not_started" ? (
          <PausedCard item={item} sessions={sessions} />
        ) : state.state === "escalated" && latestTurn ? (
          <EscalatedCard
            item={item}
            session={latestTurn}
            events={events}
            // The reply composer lives on the item page's action bar, not
            // in this pane -- same carve-out as `question` above.
            onOpenReply={() => navigate(`/work-items/${item.id}`)}
            onDismiss={() => setDismissed(latestTurn.id)}
          />
        ) : state.state === "escalating" && latestTurn ? (
          // Nobody is needed while the turn runs (deriveState's own
          // comment) -- the pill reads "agent is on it", not a demand.
          <EscalatingPill
            turn={latestTurn.attempt}
            auto={Boolean(
              events.find(
                (e) =>
                  e.type === "escalation_message" &&
                  e.payload.session_id === latestTurn.id,
              )?.payload.auto,
            )}
            busy={busy}
            err={err}
            onStop={() => run(() => api.stopEscalation(item.id), "Agent stopped")}
          />
        ) : state.needsYou ? (
          <div className="card attention-card">
            <p>Needs a decision this pane cannot make yet.</p>
            <Link className="btn btn-secondary" to={`/work-items/${item.id}`} onClick={onClose}>
              Open →
            </Link>
          </div>
        ) : null}
        {lines.length > 0 && (
          <div className="peek-log">
            {lines.map((l) => (
              <div key={l.n} className="peek-log-line">
                <span className="peek-log-time">{l.t ? clock(l.t) : ""}</span>
                {logLineText(l)}
              </div>
            ))}
          </div>
        )}
      </aside>
    </>
  );
}
