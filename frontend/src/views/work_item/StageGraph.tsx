import { useEffect, useState } from "react";
import { Flag, ShieldCheck } from "@phosphor-icons/react";
import * as api from "../../api";
import { elapsedBetween, nodeRunSpan } from "../../format";
import type { ChainNode, KraftEvent, WorkerSession, WorkItem } from "../../types";

/**
 * The clickable stage graph (UI v2 · 05): a pill per node, a link between
 * each pair, a gate flag on the link after a gated node, a shield on a pill
 * whose effective `auto_escalate` is on. Selecting a pill sets `#node=<id>`
 * (`useNodeSelection`, `index.tsx`) — the inspector and right pane follow it.
 *
 * Trimmed-node placeholders (21's dimmed `spec` with a `–` glyph, Kraft-1brd):
 * a node an attachment satisfied at intake never enters `chain_definition`,
 * so it needs the *template*'s own node list to know it existed at all.
 * `GET /templates/{id}` (`api.getTemplate`) already returns that shape, keyed
 * by `chain_template` -- fetched here rather than carried on the detail
 * payload since it is only needed for this rare state.
 */

/** A node the template lists that the live chain never got — its gate was
 *  already satisfied by an attachment at intake (`templates.materialize`). */
type TrimmedNode = { id: string; trimmed: true };

function useFullNodeList(item: WorkItem, liveNodes: ChainNode[]): (ChainNode | TrimmedNode)[] {
  const [templateIds, setTemplateIds] = useState<string[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    setTemplateIds(null);
    api
      .getTemplate(item.chain_template)
      .then((t) => {
        if (!cancelled) setTemplateIds(t.nodes.map((n) => n.id));
      })
      .catch(() => {
        if (!cancelled) setTemplateIds(null);
      });
    return () => {
      cancelled = true;
    };
  }, [item.chain_template]);

  // Only trust the template order when it actually accounts for every live
  // node -- a template edited since this item was created could otherwise
  // drop a live node off the graph entirely instead of just missing a
  // placeholder for a trimmed one.
  if (!templateIds || !liveNodes.every((n) => templateIds.includes(n.id))) {
    return liveNodes;
  }
  const byId = new Map(liveNodes.map((n) => [n.id, n]));
  return templateIds.map((id) => byId.get(id) ?? { id, trimmed: true });
}

/** Shared with `Phone.tsx`'s vertical stage list (m04) — same three states,
 *  one glyph vocabulary. */
export function nodeState(
  node: ChainNode,
  item: WorkItem,
): "current" | "done" | "todo" {
  if (node.id === item.current_node_id) return "current";
  if (item.completedNodes?.includes(node.id)) return "done";
  return "todo";
}

export function StageGraph({
  item,
  events = [],
  sessions = [],
  selected,
  onSelect,
}: {
  item: WorkItem;
  events?: KraftEvent[];
  sessions?: WorkerSession[];
  selected: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const liveNodes = (item.effective_chain ?? item.chain_definition).nodes;
  const nodes = useFullNodeList(item, liveNodes);
  return (
    <nav className="stage-graph" aria-label="chain stages">
      {nodes.map((n, i) => {
        if ("trimmed" in n) {
          return (
            <span className="stage-link-wrap" key={n.id}>
              <span
                className="stage-pill"
                data-state="trimmed"
                title={`${n.id} — skipped, its gate was already satisfied at intake`}
              >
                –
              </span>
              {i < nodes.length - 1 && <span className="stage-link" />}
            </span>
          );
        }
        const isCurrent = n.id === item.current_node_id;
        // W0.6: the gate mark follows `pending_gate`, not `auto_escalate` —
        // an item whose review node had auto-escalate overridden off lost
        // its only sign on the graph that it was waiting on a gate.
        const gated = isCurrent && !!item.pending_gate;
        const span = nodeRunSpan(n.id, events, sessions);
        const tip = [n.id, span && elapsedBetween(span.from, span.to), gated && `waiting at ${item.pending_gate}`]
          .filter(Boolean)
          .join(" · ");
        return (
        <span className="stage-link-wrap" key={n.id}>
          <button
            type="button"
            className="stage-pill"
            data-state={nodeState(n, item)}
            data-selected={n.id === selected}
            data-gate={gated || undefined}
            aria-current={isCurrent ? "step" : undefined}
            aria-pressed={n.id === selected}
            title={tip}
            onClick={() => onSelect(n.id)}
          >
            {gated && <Flag size={11} weight="fill" className="stage-pill-gate" aria-hidden />}
            {n.auto_escalate && (
              <ShieldCheck size={12} weight="fill" className="stage-pill-escalate" />
            )}
            {n.id}
            {isCurrent && item.progress && (
              <span className="stage-pill-progress">
                {item.progress.current}/{item.progress.total}
              </span>
            )}
          </button>
          {i < nodes.length - 1 && (
            <span className="stage-link">
              {n.gate_after && <Flag size={9} weight="fill" className="stage-link-gate" />}
            </span>
          )}
        </span>
        );
      })}
    </nav>
  );
}
