import { useMemo, type KeyboardEvent } from "react";
import type { ChainNode } from "./layout";
import { NodeGlyph } from "./NodeGlyph";
import { accessibleName } from "./types";
import { useRoving } from "./useRoving";
import "./graph.css";
import { tip } from "../ui/Tooltip";

/** Strip geometry, from ChainStrip.dc.html `static G`. */
export const S = { B: 30, CE: 50, CG: 34, CY: 22, PAD: 22, H: 64 } as const;

export function stripLayout(nodes: ChainNode[]) {
  let x = S.PAD;
  const items = nodes.map((node) => {
    const w = node.kind === "gate" ? S.CG : S.CE, cx = x + w / 2;
    x += w;
    return { node, cx };
  });
  const half = (n: ChainNode) => (n.kind === "gate" ? 12 : S.B / 2);
  const edges = items.slice(1).map((b, i) => {
    const a = items[i];
    return { d: `M${a.cx + half(a.node) + 3} ${S.CY} L${b.cx - half(b.node) - 3} ${S.CY}`, todo: b.node.state === "todo" };
  });
  return { items, edges, W: x + S.PAD, H: S.H };
}

type Props = { nodes: ChainNode[]; viewing: string; onOpen?: (id: string) => void; onBack?: () => void };

/** The chain bar above a node view: one row, only the viewed node labelled. */
export function ChainStrip({ nodes, viewing, onOpen, onBack }: Props) {
  const lay = useMemo(() => stripLayout(nodes), [nodes]);
  const roving = useRoving(viewing, nodes[0]?.id);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = nodes.findIndex((n) => n.id === roving.active);
    e.preventDefault();
    roving.go(nodes[Math.max(0, Math.min(nodes.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)))]?.id);
  };
  return (
    <div className="strip">
      <button type="button" className="strip-back" aria-label="Back to the chain" title="Back to the chain (Esc)" onClick={onBack}>← chain</button>
      <div className="strip-scroll">
        <div role="group" aria-label="Chain" className="strip-world" style={{ width: lay.W, height: lay.H }} onKeyDown={onKeyDown}>
          <svg width={lay.W} height={lay.H} aria-hidden="true">
            {lay.edges.map((e, i) => <path key={i} d={e.d} className={e.todo ? "strip-edge is-todo" : "strip-edge"} />)}
          </svg>
          {lay.items.map(({ node: n, cx }) => {
            const v = n.id === viewing;
            return (
              <div key={n.id} className="strip-node" style={{ left: cx - 45, top: S.CY - S.B / 2 }}>
                <button
                  ref={roving.ref(n.id)}
                  type="button"
                  tabIndex={roving.tabIndex(n.id)}
                  className="strip-btn"
                  {...tip(accessibleName(n, n.kind === "gate" ? "gate" : "node"), n.id)}
                  aria-current={v ? "step" : undefined}
                  onFocus={() => roving.go(n.id)}
                  onClick={() => onOpen?.(n.id)}
                >
                  <NodeGlyph {...n} size="sm" sel={v} prob={n.prob && !v} />
                </button>
                {v && <span className="strip-label">{n.id}</span>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
