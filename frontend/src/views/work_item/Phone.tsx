import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CaretRight, Flag, ShieldCheck } from "@phosphor-icons/react";
import * as api from "../../api";
import { Row, RowText, StatusGlyph, Tabs } from "../../components/ui";
import { deriveState } from "../../deriveState";
import { elapsedBetween, nodeRunSpan } from "../../format";
import { useStore } from "../../store";
import type { KraftEvent, WorkerSession, WorkItem, WorkItemDiff } from "../../types";
import { Changes } from "./Inspector/Changes";
import { Config } from "./Inspector/Config";
import { Documents } from "./Inspector/Documents";
import { Tasks } from "./Inspector/Tasks";
import { Diff } from "./RightPane/Diff";
import { Doc } from "./RightPane/Doc";
import { Log } from "./RightPane/Log";
import { GATE_DOC_ID } from "./selection";
import type { InspectorTab, Selection } from "./selection";
import { nodeState } from "./StageGraph";

/**
 * Mobile m04/m05 (UI v2 · 13 "Item"/"Node page"): the phone item page's
 * top bar + vertical stage list, and the node page `#node=` opens into —
 * `index.tsx` mounts these instead of `GraphSplit`/`item-split` below
 * `usePhone`'s breakpoint, not a CSS reflow of the desktop split.
 */

/** Same four buckets Board.tsx groups by, so "N of M running · swipe"
 *  counts against the group the item is actually in there. Not imported
 *  from Board.tsx: its groups are local render state (facet filters), not
 *  something this page can reach without becoming a Board dependency. */
const SWIPE_GROUPS: { label: string; test: (i: WorkItem) => boolean }[] = [
  { label: "needs you", test: (i) => deriveState(i).needsYou },
  {
    label: "running",
    test: (i) => ["running", "rate_limited", "waiting"].includes(deriveState(i).state),
  },
  { label: "not started", test: (i) => deriveState(i).state === "not_started" },
  { label: "done", test: (i) => deriveState(i).state === "done" },
];

function useSwipeNeighbors(item: WorkItem) {
  const items = useStore((s) => Object.values(s.workItems));
  const group = SWIPE_GROUPS.find((g) => g.test(item)) ?? SWIPE_GROUPS[SWIPE_GROUPS.length - 1];
  const siblings = items
    .filter((i) => group.test(i))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const index = siblings.findIndex((i) => i.id === item.id);
  return {
    label: group.label,
    index,
    total: siblings.length,
    prevId: index > 0 ? siblings[index - 1].id : null,
    nextId: index >= 0 && index < siblings.length - 1 ? siblings[index + 1].id : null,
  };
}

const SWIPE_MIN_PX = 60;

/** The phone item header (W3.1): ‹ Board and the item title, one line, 44px
 *  -- no repo, no id. A horizontal swipe on it still moves to the
 *  next/previous item in the same board group (README "Header swipe"). */
export function PhoneTopBar({ item }: { item: WorkItem }) {
  const navigate = useNavigate();
  const { prevId, nextId } = useSwipeNeighbors(item);
  const start = useRef<{ x: number; y: number } | null>(null);

  const onTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0];
    start.current = { x: t.clientX, y: t.clientY };
  };
  const onTouchEnd = (e: React.TouchEvent) => {
    const from = start.current;
    start.current = null;
    if (!from) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - from.x;
    const dy = t.clientY - from.y;
    if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) < Math.abs(dy) * 2) return;
    const targetId = dx < 0 ? nextId : prevId; // swipe left -> next item
    if (targetId) navigate(`/work-items/${targetId}`);
  };

  return (
    <div className="phone-topbar" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
      <Link className="phone-back" to="/">
        ‹ Board
      </Link>
      <span className="phone-topbar-title" title={item.title}>
        {item.title}
      </span>
    </div>
  );
}

/** A node's row duration on the phone stage list: "now" while it's the
 *  running current node, elapsed wall time once it has a start and an end,
 *  "–" for a node that has not run yet — the same three states `StageGraph`
 *  already draws as pill colours, spelled out as a duration instead. */
function nodeDuration(
  events: KraftEvent[],
  sessions: WorkerSession[],
  nodeId: string,
  state: "current" | "done" | "todo",
): string {
  if (state === "todo") return "–";
  // The same helper and inputs as the desktop hero (W0.4): this list said
  // "now" while the hero said "-31317s" for the same node.
  const span = nodeRunSpan(nodeId, events, sessions);
  return span ? elapsedBetween(span.from, span.to) : "–";
}

/** m04's "CHAIN · TAP A STAGE" list — StageGraph's pills as a vertical,
 *  full-width, 44px-row list, the phone's tap target for the node page. */
export function PhoneStageList({
  item,
  events,
  sessions = [],
  onSelect,
}: {
  item: WorkItem;
  events: KraftEvent[];
  sessions?: WorkerSession[];
  onSelect: (nodeId: string) => void;
}) {
  const nodes = (item.effective_chain ?? item.chain_definition).nodes;
  return (
    <div className="phone-stage-list" data-testid="phone-stage-list">
      <p className="section-label">Chain · tap a stage</p>
      {nodes.map((n) => {
        const state = nodeState(n, item);
        const glyph = state === "done" ? "done" : state === "current" ? "running" : "pending";
        return (
          <Row
            key={n.id}
            data-testid={`phone-stage-${n.id}`}
            data-state={state}
            onClick={() => onSelect(n.id)}
            columns="22px 1fr auto 16px"
          >
            <StatusGlyph status={glyph} />
            <RowText
              title={
                <>
                  {n.id}
                  {n.auto_escalate && <ShieldCheck size={12} weight="fill" className="stage-pill-escalate" />}
                  {n.gate_after && <Flag size={9} weight="fill" />}
                </>
              }
            />
            <span className="row-sub">{nodeDuration(events, sessions, n.id, state)}</span>
            <CaretRight size={14} />
          </Row>
        );
      })}
    </div>
  );
}

/** m05: tap a stage → a full-screen page with its own back button, a node
 *  meta line, and Tasks/Changes/Documents/Config tabs — each stacked as
 *  list-then-detail instead of the desktop split, and no Timeline tab
 *  (mobile-core notes "Node page": four tabs, not five). Reuses the same
 *  `Inspector`/`RightPane` subcomponents rather than a phone-only fork of
 *  each one's logic. */
export function PhoneNode({
  item,
  sessions,
  events,
  nodeId,
  tab,
  onTabChange,
  selection,
  onSelect,
  onToggleMaximize,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
  events: KraftEvent[];
  nodeId: string;
  tab: InspectorTab;
  onTabChange: (t: InspectorTab) => void;
  selection: Selection;
  onSelect: (s: Selection) => void;
  /** Maximize is a page layout (43) on the phone too — index.tsx renders it. */
  onToggleMaximize?: () => void;
}) {
  const navigate = useNavigate();
  const chain = item.effective_chain ?? item.chain_definition;
  const node = chain.nodes.find((n) => n.id === nodeId) ?? null;
  const at = chain.nodes.findIndex((n) => n.id === nodeId);
  const nodeSessions = sessions.filter((s) => s.node_id === nodeId);
  const running = nodeSessions.find((s) => s.status === "running");
  const span = nodeRunSpan(nodeId, events, sessions);
  const runtime = running && span ? elapsedBetween(span.from, span.to) : null;

  // The phone page (m05) is its own render path, separate from the desktop
  // split index.tsx fetches the diff for — fetch its own copy the way the
  // desktop tree and pane used to before Task 4 lifted theirs.
  const [diff, setDiff] = useState<WorkItemDiff | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .getWorkItemDiff(item.id)
      .then((d) => alive && setDiff(d))
      .catch((e) => alive && setDiffError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [item.id]);

  return (
    <div className="phone-node" data-testid="phone-node-page">
      {/* The node page's header: back to the item, labelled with its title
          (W3.7), never the id. */}
      <div className="phone-topbar">
        <button className="phone-back phone-back-title" onClick={() => navigate(`/work-items/${item.id}`)}>
          ‹ {item.title}
        </button>
      </div>
      <div className="phone-node-head">
        <span className="phone-node-title">
          {nodeId}
          {node?.auto_escalate && <ShieldCheck size={14} weight="fill" className="stage-pill-escalate" />}
        </span>
        <span className="phone-node-meta">
          {at >= 0 && `node ${at + 1} of ${chain.nodes.length}`}
          {running && " · running"}
          {item.fixCycle != null && ` · fix cycle ${item.fixCycle}`}
          {runtime && ` · ${runtime}`}
        </span>
      </div>
      <Tabs
        value={tab}
        onChange={(t) => onTabChange(t as InspectorTab)}
        tabs={[
          // Sessions on this node, as the pane's own eyebrow counts them (W0.8).
          { id: "tasks", label: "Tasks", count: nodeSessions.length },
          { id: "changes", label: "Changes" },
          { id: "documents", label: "Documents" },
          { id: "config", label: "Config" },
        ]}
      />
      <div className="phone-node-body">
        {tab === "tasks" && (
          <>
            <Tasks
              item={item}
              sessions={sessions}
              events={events}
              nodeId={nodeId}
              selected={selection.kind === "session" ? selection.id : null}
              onSelect={(id) => onSelect({ kind: "session", id })}
            />
            {selection.kind === "session" && selection.id ? (
              <Log sessionId={selection.id} maximized={false} onToggleMaximize={onToggleMaximize} />
            ) : (
              <p className="empty pane">select a task to view its log</p>
            )}
          </>
        )}
        {tab === "changes" && (
          <>
            <Changes
              diff={diff}
              diffError={diffError}
              selected={selection.kind === "file" ? selection.id : null}
              onSelect={(id) => onSelect({ kind: "file", id })}
            />
            <Diff diff={diff} diffError={diffError} selectedFile={selection.kind === "file" ? selection.id : null} />
          </>
        )}
        {tab === "documents" && (
          <>
            <Documents
              workItemId={item.id}
              eventCount={events.length}
              selected={selection.kind === "document" ? selection.id : null}
              onSelect={(id) => onSelect({ kind: "document", id })}
              preselectPath={item.gate_artifact}
              gatePending={!!item.pending_gate}
              gateArtifactPending={!!item.pending_gate && !!item.gate_artifact}
            />
            {selection.kind === "document" && selection.id ? (
              <Doc
                source={
                  selection.id === GATE_DOC_ID
                    ? { kind: "artifact", workItemId: item.id }
                    : { kind: "document", id: selection.id }
                }
                item={item}
              />
            ) : (
              <p className="empty pane">select a document to view it</p>
            )}
          </>
        )}
        {tab === "config" && <Config item={item} nodeId={nodeId} />}
      </div>
    </div>
  );
}
