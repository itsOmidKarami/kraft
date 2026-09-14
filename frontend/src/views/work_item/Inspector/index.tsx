import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Tabs } from "../../../components/ui";
import { elapsedBetween, nodeRunSpan } from "../../../format";
import type { KraftEvent, WorkerSession, WorkItem, WorkItemDiff } from "../../../types";
import { groupByNode } from "../timelineHelpers";
import { Changes } from "./Changes";
import { Config } from "./Config";
import { Documents } from "./Documents";
import { Tasks, type Scope } from "./Tasks";
import { Timeline } from "./Timeline";
import type { InspectorTab, Selection } from "../selection";

/**
 * The 340px left column (UI v2 · 05, 11–15): a header naming the selected
 * node, `Tasks · Changes · Documents · Timeline · Config` tabs with counts,
 * and the list for whichever is active. `RightPane/index.tsx` shows the
 * selected row.
 *
 * ponytail: the per-list filter input + scope toggle (tree/flat for
 * Changes) the notes mention is skipped — Tasks already has a this-node/all
 * toggle, which is the one scope switch that matters without a search box.
 * Filed as Kraft-toqq if a real item's list count makes one
 * worth adding.
 */
export function Inspector({
  item,
  sessions,
  events,
  nodeId,
  tab,
  onTabChange,
  selection,
  onSelect,
  diff,
  diffError,
  headExtra,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
  events: KraftEvent[];
  nodeId: string | null;
  tab: InspectorTab;
  onTabChange: (t: InspectorTab) => void;
  selection: Selection;
  onSelect: (s: Selection) => void;
  diff: WorkItemDiff | null;
  diffError: string | null;
  /** Right end of the head row — the tablet List / Detail switch (W2.2). */
  headExtra?: ReactNode;
}) {
  const nodeSessions = sessions.filter((s) => s.node_id === nodeId);
  const running = nodeSessions.find((s) => s.status === "running");
  // Same helper and inputs as the hero (W0.4), so the two never disagree.
  const span = nodeRunSpan(nodeId, events, sessions);
  const runtime = span ? elapsedBetween(span.from, span.to) : null;

  // Two sticky siblings at the same `top: 0` stack on each other -- the tabs
  // need to sit below the head's actual height, not a guessed 40px.
  const headRef = useRef<HTMLDivElement>(null);
  const [headHeight, setHeadHeight] = useState(40);
  // One this node · all scope for the lists that have one (W11 · F.1); the
  // stage-graph pill is what "this node" follows.
  const [scope, setScope] = useState<Scope>("node");
  // Reported by the Documents list once it has loaded; follows the scope (G.4).
  const [docCount, setDocCount] = useState<number | undefined>(undefined);
  const nodeEvents = groupByNode(events).find((g) => g.node === nodeId)?.events.length ?? 0;
  // W14 · D: a zero explained. Sessions whose hook is not an agent never write a
  // summary, so a node run by a test command has tasks and no documents (Kraft-1s6u2).
  const docSessions = scope === "node" && nodeId ? nodeSessions.length : sessions.length;
  const docHint =
    docCount === 0 && docSessions > 0
      ? `Documents · 0 · ${docSessions === 1 ? "1 session has" : `${docSessions} sessions have`} no summary`
      : undefined;
  useLayoutEffect(() => {
    if (headRef.current) setHeadHeight(headRef.current.offsetHeight);
  });

  return (
    <aside
      className="inspector"
      data-testid="inspector"
      style={{ "--inspector-head-h": `${headHeight}px` } as CSSProperties}
    >
      <div className="inspector-head" ref={headRef}>
        <span className="mono">{nodeId ?? "—"}</span>
        {running && <span className="row-state" data-status="running">running{runtime ? ` · ${runtime}` : ""}</span>}
        {headExtra && <span className="inspector-head-extra">{headExtra}</span>}
      </div>
      <Tabs
        value={tab}
        onChange={(t) => onTabChange(t as InspectorTab)}
        tabs={[
          // Sessions on the selected node — the same N as the pane's
          // "SESSIONS · N" eyebrow (W0.8).
          { id: "tasks", label: "Tasks", count: nodeId ? nodeSessions.length : sessions.length },
          { id: "changes", label: "Changes" },
          { id: "documents", label: "Documents", count: docCount, hint: docHint },
          // Follows the scope (F.2): this node's events, or all of them.
          { id: "timeline", label: "Timeline", count: scope === "node" ? nodeEvents : events.length },
          { id: "config", label: "Config" },
        ]}
      />
      <div className="inspector-body">
        {tab === "tasks" && (
          <Tasks
            item={item}
            sessions={sessions}
            events={events}
            nodeId={nodeId}
            selected={selection.kind === "session" ? selection.id : null}
            onSelect={(id) => onSelect({ kind: "session", id })}
            scope={scope}
            onScope={setScope}
          />
        )}
        {tab === "changes" && (
          <Changes
            diff={diff}
            diffError={diffError}
            selected={selection.kind === "file" ? selection.id : null}
            onSelect={(id) => onSelect({ kind: "file", id })}
          />
        )}
        {tab === "documents" && (
          <Documents
            workItemId={item.id}
            eventCount={events.length}
            selected={selection.kind === "document" ? selection.id : null}
            onSelect={(id) => onSelect({ kind: "document", id })}
            preselectPath={item.gate_artifact}
            gatePending={!!item.pending_gate}
            gateArtifactPending={!!item.pending_gate && !!item.gate_artifact}
            nodeId={nodeId}
            nodes={(item.effective_chain ?? item.chain_definition).nodes}
            scope={scope}
            onScope={setScope}
            onCount={setDocCount}
            onShowTasks={() => onTabChange("tasks")}
            item={item}
            sessions={sessions}
          />
        )}
        {tab === "timeline" && (
          <Timeline
            events={events}
            sessions={sessions}
            nodeId={nodeId}
            scope={scope}
            onScope={setScope}
            selected={selection.kind === "timeline-node" ? selection.id : null}
            onSelect={(id) => onSelect({ kind: "timeline-node", id })}
          />
        )}
        {tab === "config" && <Config item={item} nodeId={nodeId} />}
      </div>
    </aside>
  );
}
