import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowSquareOut, X } from "@phosphor-icons/react";
import * as api from "../api";
import { useStore } from "../store";
import { deriveState } from "../deriveState";
import { StatusGlyph, TaskBar, TaskLine } from "./ui";
import { ago, clock, logLineText, repoName } from "../format";
import { ShortId } from "./ShortId";
import type { LogLine } from "../types";
import { dismissedTurnId } from "../views/work_item/ActionBar/EscalationCard";
import { ItemCard, type ComposerKind } from "../views/work_item/ActionBar/ItemCard";
import { SELECTION_KEY } from "../views/work_item/useItemUrlState";

/**
 * The board's peek pane (UI v2 · 05): the row's own detail, without leaving
 * the board. Same data the detail screen shows for the current node --
 * `hydrateItem` (detail endpoint) plus the last few log lines of the current
 * session -- no new endpoint.
 *
 * Its action card is the item page's own (W11 · I), rendered narrow.
 */
export function PeekPane({
  id,
  onClose,
  compose,
}: {
  id: string;
  onClose: () => void;
  /** The composer a board row's button asked for (W11 · B.3). */
  compose?: ComposerKind;
}) {
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
  const [full, setFull] = useState(false);
  const navigate = useNavigate();

  if (!item) return null;
  // Same events/sessions the header tag and the card must agree on (Kraft-av3t).
  const rawState = deriveState(item, sessions, events);
  const latestTurn = sessions
    .filter((s) => s.hook_point === "escalation")
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .at(-1);
  // Same idiom as the card: a dismissed escalated turn is client-side state
  // (localStorage), so the header re-derives without it too.
  const state =
    rawState.state === "escalated" && latestTurn && dismissedTurnId(id) === latestTurn.id
      ? deriveState(item, [], events)
      : rawState;
  // The card's item-page doors: close the peek first (Kraft-3e16), then go.
  const openItem = (hash = "") => {
    onClose();
    navigate(`/work-items/${item.id}${hash}`);
  };

  const nodes = item.chain_definition.nodes;
  const rawIndex = item.current_node_id ? nodes.findIndex((n) => n.id === item.current_node_id) : -1;
  // A null current_node_id means two different things: a finished item ran
  // every node and cleared it (nodes.length - 1, the last one, is right),
  // but a never-started item hasn't reached node 0 yet -- the same fallback
  // there would point at the chain's last node instead of its first
  // (Header.tsx's runLine already special-cases this for the same reason).
  const nodeIndex = rawIndex === -1 ? (state.state === "not_started" ? 0 : nodes.length - 1) : rawIndex;
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
      <aside className="peek-pane" aria-label="peek" data-full={full || undefined}>
        {/* Phone (W3.6): a 60vh bottom sheet; the handle takes it full height. */}
        <button
          type="button"
          className="peek-handle phone-only"
          aria-label={full ? "Collapse sheet" : "Expand sheet"}
          aria-expanded={full}
          onClick={() => setFull((v) => !v)}
        />
        {/* W10.D: one grid that never scrolls sideways -- id, state, MR and
            the repo · template meta wrap inside the first cell; Open → and ✕
            keep their own columns at any pane width. */}
        <div className="peek-head">
          <div className="peek-head-id">
          <ShortId id={item.id} />
          {/* W8.8: words, not the identifier ("not started", never "not_started"). */}
          <span className="tag tag-outline tag-tight">{state.state.replace(/_/g, " ")}</span>
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
          <span className="peek-head-sub">
            <span title={item.repo}>{repoName(item.repo)}</span> · <span>{item.chain_template}</span>
          </span>
          </div>
          {/* Kraft-3e16 (spec §2.2): `onClose` (Board's `setPeek(null)`,
              written with `replace: true`) clears the board's own ?peek
              before Link's push runs — Link fires this handler first, then
              navigates unless it was prevented. So the entry Back returns
              to is a clean board, not one with the peek still open. */}
          <Link to={`/work-items/${item.id}`} className="peek-open" onClick={onClose}>
            Open →
          </Link>
          <button className="btn btn-ghost peek-close" title="Close (Esc)" aria-label="Close" onClick={onClose}>
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
        {/* The item page's own card, narrow (W11 · I): the same buttons, More
            actions and inline composers on every state, so the two never
            drift apart. Keyed on the composer a board row asked for, so a
            second row's button opens its own. */}
        <ItemCard
          key={`${item.id}:${compose ?? ""}`}
          variant="peek"
          item={item}
          sessions={sessions}
          events={events}
          initialOpen={compose}
          onOpenItem={() => openItem()}
          onReviewChanges={() => openItem("#tab=changes")}
          onEditChain={() => openItem("#tab=config")}
          reviewHref={(node, tab, sel) => {
            const p = new URLSearchParams({ node, tab });
            if (SELECTION_KEY[tab] && sel) p.set(SELECTION_KEY[tab], sel);
            return `/work-items/${item.id}#${p}`;
          }}
        />
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
