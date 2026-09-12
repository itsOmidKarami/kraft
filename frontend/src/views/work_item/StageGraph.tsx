import { Flag, ShieldCheck } from "@phosphor-icons/react";
import type { ChainNode, WorkItem } from "../../types";

/**
 * The clickable stage graph (UI v2 · 05): a pill per node, a link between
 * each pair, a gate flag on the link after a gated node, a shield on a pill
 * whose effective `auto_escalate` is on. Selecting a pill sets `#node=<id>`
 * (`useNodeSelection`, `index.tsx`) — the inspector and right pane follow it.
 *
 * Trimmed-node placeholders (21's dimmed `spec` with a `–` glyph) are out of
 * scope here: rendering one needs the *template's* full node list, which the
 * detail payload does not carry and fetching `/templates/{id}` per page load
 * is a second round trip for a rare state. Filed as Kraft-1brd rather than built.
 */

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
  selected,
  onSelect,
}: {
  item: WorkItem;
  selected: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const nodes = (item.effective_chain ?? item.chain_definition).nodes;
  return (
    <nav className="stage-graph" aria-label="chain stages">
      {nodes.map((n, i) => {
        const isCurrent = n.id === item.current_node_id;
        return (
        <span className="stage-link-wrap" key={n.id}>
          <button
            type="button"
            className="stage-pill"
            data-state={nodeState(n, item)}
            data-selected={n.id === selected}
            aria-current={isCurrent ? "step" : undefined}
            aria-pressed={n.id === selected}
            onClick={() => onSelect(n.id)}
          >
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
