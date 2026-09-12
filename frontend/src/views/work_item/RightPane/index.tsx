import { useCallback } from "react";
import type { KraftEvent, WorkerSession, WorkItem, WorkItemDiff } from "../../../types";
import type { InspectorTab, Selection } from "../selection";
import { usePhone } from "../usePhone";
import { ConfigPane } from "./ConfigPane";
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
  diff,
  diffError,
  onSelect,
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
  diff: WorkItemDiff | null;
  diffError: string | null;
  onSelect: (s: Selection, opts?: { replace?: boolean }) => void;
  onViewLog: (sessionId: string) => void;
  maximized: boolean;
  onToggleMaximize: () => void;
}) {
  const phone = usePhone();
  // Stable identity: an inline arrow here would give Diff's effect a new
  // `onVisibleFile` every render, tearing down and re-observing the
  // IntersectionObserver each time (see Diff.tsx). `replace: true` because
  // scroll fires per file crossed — pushing each would leave Back unable
  // to exit the page.
  const onVisibleFile = useCallback(
    (path: string) => onSelect({ kind: "file", id: path }, { replace: true }),
    [onSelect],
  );
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
        capLines={phone ? 8 : undefined}
        taskTotal={item.progress?.total}
      />
    );
  }

  if (tab === "changes") {
    return (
      <Diff
        diff={diff}
        diffError={diffError}
        selectedFile={selection.kind === "file" ? selection.id : null}
        onVisibleFile={onVisibleFile}
        maximized={maximized}
        onToggleMaximize={onToggleMaximize}
      />
    );
  }

  if (tab === "documents") {
    if (selection.kind !== "document" || !selection.id) {
      return <p className="empty pane">select a document to view it</p>;
    }
    return <Doc id={selection.id} item={item} maximized={maximized} onToggleMaximize={onToggleMaximize} />;
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
