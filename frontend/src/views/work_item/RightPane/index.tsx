import type { KraftEvent, WorkerSession, WorkItem, WorkItemDiff } from "../../../types";
import type { InspectorTab, Selection } from "../selection";
import { usePhone } from "../usePhone";
import { ConfigPane } from "./ConfigPane";
import { Diff } from "./Diff";
import { Doc, type DocSource } from "./Doc";
import { GATE_DOC_ID } from "../selection";
import { parseTimelineSelection, selectionNode } from "../timelineHelpers";
import { Events } from "./Events";
import { Log } from "./Log";

/**
 * The right pane (UI v2 · 05, 12–15): shows whatever the Inspector's
 * selected row is. No modal opens from here for a log, a diff or a
 * document — `LogModal`/`DiffModal`/`DocumentModal` are gone.
 */
export function RightPane({
  item,
  events,
  sessions,
  nodeId,
  tab,
  selection,
  diff,
  diffError,
  onViewLog,
  onTimelineSelect,
  maximized,
  onToggleMaximize,
}: {
  item: WorkItem;
  events: KraftEvent[];
  sessions: WorkerSession[];
  nodeId: string | null;
  tab: InspectorTab;
  selection: Selection;
  diff: WorkItemDiff | null;
  diffError: string | null;
  onViewLog: (sessionId: string) => void;
  /** The Timeline stream's session rows select in place (W14 · A). */
  onTimelineSelect?: (id: string) => void;
  maximized: boolean;
  onToggleMaximize: () => void;
}) {
  const phone = usePhone();
  if (tab === "config") return <ConfigPane item={item} nodeId={nodeId} />;

  if (tab === "tasks") {
    if (selection.kind !== "session" || !selection.id) {
      return <p className="empty pane">select a task to view its log</p>;
    }
    return (
      <Log
        sessionId={selection.id}
        maximized={maximized}
        onToggleMaximize={onToggleMaximize}
        capLines={phone && !maximized ? 8 : undefined}
      />
    );
  }

  if (tab === "changes") {
    return (
      <Diff
        diff={diff}
        diffError={diffError}
        selectedFile={selection.kind === "file" ? selection.id : null}
        maximized={maximized}
        onToggleMaximize={onToggleMaximize}
      />
    );
  }

  if (tab === "documents") {
    if (selection.kind !== "document" || !selection.id) {
      return <p className="empty pane">select a document to view it</p>;
    }
    const source: DocSource =
      selection.id === GATE_DOC_ID ? { kind: "artifact", workItemId: item.id } : { kind: "document", id: selection.id };
    return <Doc source={source} item={item} sessions={sessions} maximized={maximized} onToggleMaximize={onToggleMaximize} />;
  }

  // timeline: `session:<id>`, `round:<node>:<n>`, `event:<seq>`, or W11's
  // `node:<seq>` / bare `node` (W13 · C.4).
  const tsel = parseTimelineSelection(selection.kind === "timeline-node" ? selection.id : null);
  return (
    <Events
      events={events}
      sessions={sessions}
      nodeId={selectionNode(tsel, events, sessions) ?? nodeId}
      selection={tsel}
      onSelect={onTimelineSelect}
      onViewLog={onViewLog}
    />
  );
}
