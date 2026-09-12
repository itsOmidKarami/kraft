import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";
import type { InspectorTab, Selection } from "../selection";
import { usePhone } from "../usePhone";
import { Diff } from "./Diff";
import { Doc } from "./Doc";
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
  onViewLog,
  maximized,
  onToggleMaximize,
}: {
  item: WorkItem;
  events: KraftEvent[];
  sessions: WorkerSession[];
  nodeId: string | null;
  tab: InspectorTab;
  selection: Selection;
  onViewLog: (sessionId: string) => void;
  maximized: boolean;
  onToggleMaximize: () => void;
}) {
  const phone = usePhone();
  if (tab === "config") return null; // Config's own right column is inline

  if (tab === "tasks") {
    if (selection.kind !== "session" || !selection.id) {
      return <p className="empty pane">select a task to view its log</p>;
    }
    return (
      <Log
        sessionId={selection.id}
        maximized={maximized}
        onToggleMaximize={onToggleMaximize}
        capLines={phone ? 8 : undefined}
        taskTotal={item.progress?.total}
      />
    );
  }

  if (tab === "changes") {
    return <Diff workItemId={item.id} selectedFile={selection.kind === "file" ? selection.id : null} />;
  }

  if (tab === "documents") {
    if (selection.kind !== "document" || !selection.id) {
      return <p className="empty pane">select a document to view it</p>;
    }
    return <Doc id={selection.id} />;
  }

  // timeline
  return (
    <Events
      events={events}
      sessions={sessions}
      nodeId={selection.kind === "timeline-node" && selection.id ? selection.id : nodeId}
      onViewLog={onViewLog}
    />
  );
}
