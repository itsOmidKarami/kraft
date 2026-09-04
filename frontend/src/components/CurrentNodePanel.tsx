import { useState } from "react";
import { Row, RowState, RowText, StatusGlyph } from "./ui";
import { LogModal } from "./LogModal";
import type { WorkItem, WorkerSession } from "../types";

/**
 * The Tasks tab: one spec-§2 row per session on the current node. The gate card
 * is not here — it replaces the control row on the page above (design 4a).
 */
export function CurrentNodePanel({
  item,
  sessions,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
}) {
  const rows = sessions.filter((s) => s.node_id === item.current_node_id);
  const [logSid, setLogSid] = useState<string | null>(null);

  if (rows.length === 0) return <p className="empty">no tasks running on this node</p>;

  return (
    <div className="current-node">
      {rows.map((s) => (
        <Row key={s.id} data-testid={`session-${s.id}`} data-status={s.status}>
          <StatusGlyph status={s.status} />
          <RowText
            title={s.hook_point}
            sub={
              s.hook_point === "on.implementation.start" && item.fixCycle != null
                ? `fix · cycle ${item.fixCycle}`
                : `attempt ${s.attempt}`
            }
          />
          <RowState status={s.status} />
          <button className="btn btn-ghost row-action" onClick={() => setLogSid(s.id)}>
            view log
          </button>
        </Row>
      ))}
      {logSid && <LogModal sessionId={logSid} onClose={() => setLogSid(null)} />}
    </div>
  );
}
