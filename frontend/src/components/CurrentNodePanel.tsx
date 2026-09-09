import { useState } from "react";
import { Row, RowState, RowText, StatusGlyph } from "./ui";
import { LogModal } from "./LogModal";
import { elapsed, tokens, usd } from "../format";
import type { WorkItem, WorkerSession } from "../types";

/**
 * The Tasks tab: every session the item has run, grouped by node in chain
 * order, the current node open and the rest collapsed.
 *
 * Not only the current node (Kraft-n9gw): almost every node runs one task, so
 * the filtered version was a one-row tab, and between nodes it claimed there
 * were no tasks at all while the payload held every session. The name is kept
 * — renaming it is churn across imports and tests for no reader benefit.
 *
 * The gate card is not here — it replaces the control row on the page above
 * (design 4a).
 */

/** "41.2k tokens · $1.10 · 4m", each part dropped when the agent reported none. */
function metricsOf(s: WorkerSession): string {
  const parts: string[] = [];
  if (s.tokens_in != null || s.tokens_out != null) {
    parts.push(`${tokens((s.tokens_in ?? 0) + (s.tokens_out ?? 0))} tokens`);
  }
  if (s.cost_usd != null) parts.push(usd(s.cost_usd));
  if (s.wall_ms != null) parts.push(elapsed(s.wall_ms));
  return parts.join(" · ");
}

export function CurrentNodePanel({
  item,
  sessions,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
}) {
  const [logSid, setLogSid] = useState<string | null>(null);

  // Insertion order is first-seen order, which is what a node the chain does
  // not name (a rerun under a revised chain) falls back to.
  const byNode = new Map<string, WorkerSession[]>();
  for (const s of sessions) {
    if (!byNode.has(s.node_id)) byNode.set(s.node_id, []);
    byNode.get(s.node_id)!.push(s);
  }
  const inChain = item.chain_definition.nodes.map((n) => n.id).filter((id) => byNode.has(id));
  const rest = [...byNode.keys()].filter((id) => !inChain.includes(id));
  const groups = [...inChain, ...rest];

  if (groups.length === 0) return <p className="empty">no tasks have run yet</p>;

  // The current node ordinarily has a group — but between two nodes, or once
  // the chain is done, `current_node_id` names a node with no session yet (or
  // any more). Falling through to "nothing open" would leave every group
  // collapsed with no indication why; open the most recently active one
  // instead, so the tab always shows something.
  const cur = item.current_node_id;
  const openNode = cur && byNode.has(cur) ? cur : groups[groups.length - 1];

  return (
    <div className="current-node">
      {groups.map((node) => (
        /* `<details>` is the whole interaction: no state, no toggle handler,
           keyboard-accessible for free — the same one DiffModal uses. */
        <details key={node} data-node={node} open={node === openNode}>
          <summary>
            <span className="node-group-head">
              <span className="row-title">{node}</span>
              <span className="row-sub">
                {byNode.get(node)!.length} task
                {byNode.get(node)!.length === 1 ? "" : "s"}
              </span>
            </span>
          </summary>
          {byNode.get(node)!.map((s) => {
            const metrics = metricsOf(s);
            const attempt =
              s.hook_point === "on.implementation.start" && item.fixCycle != null
                ? `fix · cycle ${item.fixCycle}`
                : `attempt ${s.attempt}`;
            return (
              <Row key={s.id} data-testid={`session-${s.id}`} data-status={s.status}>
                <StatusGlyph status={s.status} />
                <RowText title={s.hook_point} sub={metrics ? `${attempt} · ${metrics}` : attempt} />
                <RowState status={s.status} />
                <button className="btn btn-ghost row-action" onClick={() => setLogSid(s.id)}>
                  view log
                </button>
              </Row>
            );
          })}
        </details>
      ))}
      {logSid && <LogModal sessionId={logSid} onClose={() => setLogSid(null)} />}
    </div>
  );
}
