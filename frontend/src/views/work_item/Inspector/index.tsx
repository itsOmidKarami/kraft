import { Tabs } from "../../../components/ui";
import { elapsed } from "../../../format";
import type { KraftEvent, WorkerSession, WorkItem } from "../../../types";
import { Changes } from "./Changes";
import { Config } from "./Config";
import { Documents } from "./Documents";
import { Tasks } from "./Tasks";
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
}: {
  item: WorkItem;
  sessions: WorkerSession[];
  events: KraftEvent[];
  nodeId: string | null;
  tab: InspectorTab;
  onTabChange: (t: InspectorTab) => void;
  selection: Selection;
  onSelect: (s: Selection) => void;
}) {
  const nodeSessions = sessions.filter((s) => s.node_id === nodeId);
  const running = nodeSessions.find((s) => s.status === "running");
  const runtime = running?.started_at ? elapsed(Date.now() - Date.parse(running.started_at)) : null;

  return (
    <aside className="inspector" data-testid="inspector">
      <div className="inspector-head">
        <span className="mono">{nodeId ?? "—"}</span>
        {running && <span className="row-state" data-status="running">running{runtime ? ` · ${runtime}` : ""}</span>}
      </div>
      <Tabs
        value={tab}
        onChange={(t) => onTabChange(t as InspectorTab)}
        tabs={[
          { id: "tasks", label: "Tasks", count: sessions.length },
          { id: "changes", label: "Changes" },
          { id: "documents", label: "Documents" },
          { id: "timeline", label: "Timeline", count: events.length },
          { id: "config", label: "Config" },
        ]}
      />
      <div className="inspector-body">
        {tab === "tasks" && (
          <Tasks
            item={item}
            sessions={sessions}
            nodeId={nodeId}
            selected={selection.kind === "session" ? selection.id : null}
            onSelect={(id) => onSelect({ kind: "session", id })}
          />
        )}
        {tab === "changes" && (
          <Changes
            workItemId={item.id}
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
          />
        )}
        {tab === "timeline" && (
          <Timeline
            events={events}
            selected={selection.kind === "timeline-node" && selection.id ? selection.id : nodeId}
            onSelect={(id) => onSelect({ kind: "timeline-node", id })}
          />
        )}
        {tab === "config" && <Config item={item} nodeId={nodeId} />}
      </div>
    </aside>
  );
}
