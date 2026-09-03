import type { WorkItem } from "../types";

export function ChainStrip({
  item,
  size,
}: {
  item: WorkItem;
  size: "sm" | "lg";
}) {
  const done = new Set(item.completedNodes ?? []);
  return (
    <ol className={`chain-strip ${size}`}>
      {item.chain_definition.nodes.map((n) => {
        const current = n.id === item.current_node_id;
        const cls = [
          "chain-node",
          current && "current",
          done.has(n.id) && "done",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <li key={n.id} data-testid={`node-${n.id}`} data-node={n.id} className={cls}>
            <span className="label">{n.id}</span>
            {n.gate_after && (
              <span className="gate" title={n.gate_after}>
                ⚑
              </span>
            )}
            {n.tasks.length > 1 && <span className="count">{n.tasks.length}</span>}
            {current && n.fix_loop && item.fixCycle != null && (
              <span className="fix">fix · {item.fixCycle}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
