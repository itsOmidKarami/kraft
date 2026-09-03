import { useState } from "react";
import type { WorkItem, WorkerSession } from "../types";
import { Gate } from "./Gate";
import { LogModal } from "./LogModal";

export function CurrentNodePanel({
  item,
  sessions,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
}) {
  const node = item.chain_definition.nodes.find((n) => n.id === item.current_node_id);
  const rows = sessions.filter((s) => s.node_id === item.current_node_id);
  const nextNode = item.chain_definition.nodes[
    item.chain_definition.nodes.findIndex((n) => n.id === item.current_node_id) + 1
  ];
  const awaitingGate =
    !!node?.gate_after &&
    rows.length > 0 &&
    rows.every((s) => s.status === "done") &&
    !sessions.some((s) => nextNode && s.node_id === nextNode.id);

  const [logSid, setLogSid] = useState<string | null>(null);

  return (
    <section className="current-node">
      <h3>{item.current_node_id}</h3>
      <ul>
        {rows.map((s) => (
          <li key={s.id} data-testid={`session-${s.id}`} className="session-row" data-status={s.status}>
            <span className="hook">{s.hook_point}</span>
            <span className="chip" data-status={s.status}>{s.status}</span>
            {s.hook_point === "on.implementation.start" && item.fixCycle != null && (
              <span className="fix-badge">fix · cycle {item.fixCycle}</span>
            )}
            <button onClick={() => setLogSid(s.id)}>view log</button>
          </li>
        ))}
      </ul>
      {awaitingGate && node?.gate_after && <Gate item={item} gate={node.gate_after} />}
      {logSid && <LogModal sessionId={logSid} onClose={() => setLogSid(null)} />}
    </section>
  );
}
