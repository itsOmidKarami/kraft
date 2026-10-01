import { useEffect, useRef } from "react";
import { NodeGlyph } from "../../graph/NodeGlyph";
import type { ChainNode } from "../../graph/layout";

/** Every node as a chip under the header (W17 brief D.1): at least 96px wide and
 *  44px high, the label wrapping instead of cutting (R10), the current one scrolled into view. */
export function Strip({ nodes, current, onPick }: { nodes: ChainNode[]; current: string; onPick: (id: string) => void }) {
  const on = useRef<HTMLButtonElement>(null);
  useEffect(() => on.current?.scrollIntoView?.({ inline: "center", block: "nearest" }), [current]);
  return (
    <div className="ph-strip" role="group" aria-label="Chain">
      {nodes.map((n) => (
        <button key={n.id} ref={n.id === current ? on : undefined} type="button" className={`ph-strip-chip${n.id === current ? " ph-is-on" : ""}`} aria-current={n.id === current ? "true" : undefined} onClick={() => onPick(n.id)}>
          <NodeGlyph kind={n.kind} size="sm" state={n.state} icon={n.icon} running={n.running} paused={n.paused} capped={n.capped} />
          <span className="ph-strip-label">{n.id}</span>
        </button>
      ))}
    </div>
  );
}
