import type { WorkItem, WorkerSession } from "./types";

/**
 * The gate this item is waiting on, or null.
 *
 * Inferred client-side (spec §7): the current node has a `gate_after`, every
 * session it started has ended clean, and nothing has started on the next node.
 * There is no server-side "awaiting gate" flag to read instead.
 */
export function awaitingGate(item: WorkItem, sessions: WorkerSession[]): string | null {
  const nodes = item.chain_definition.nodes;
  const at = nodes.findIndex((n) => n.id === item.current_node_id);
  const node = nodes[at];
  if (!node?.gate_after) return null;
  const rows = sessions.filter((s) => s.node_id === node.id);
  if (rows.length === 0 || !rows.every((s) => s.status === "done")) return null;
  const next = nodes[at + 1];
  if (next && sessions.some((s) => s.node_id === next.id)) return null;
  return node.gate_after;
}
