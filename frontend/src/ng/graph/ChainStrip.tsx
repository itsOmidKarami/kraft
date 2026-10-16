import { Fragment, useEffect, useRef, type KeyboardEvent } from "react";
import { displayName } from "../board/rowText";
import type { ChainNode } from "./layout";
import { tone } from "./Minimap";
import { accessibleName } from "./types";
import { useRoving } from "./useRoving";
import "./graph.css";
import { tip } from "../ui/Tooltip";

type Props = {
  nodes: ChainNode[];
  viewing: string;
  /** Nodes named in words ("approve spec"), the item's; an editor names them by id. */
  names?: boolean;
  onOpen?: (id: string) => void;
  onBack?: () => void;
};

/** The chain bar above a node view: the whole chain as the overview draws it, one glyph per node in its state, the
 *  node in view ringed; a click goes to that node. */
export function ChainStrip({ nodes, viewing, names = false, onOpen, onBack }: Props) {
  const roving = useRoving(viewing, nodes[0]?.id);
  // A strip wider than its bar scrolls to the node in view.
  const world = useRef<HTMLDivElement>(null);
  useEffect(() => {
    world.current?.querySelector("[aria-current]")?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [viewing]);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = nodes.findIndex((n) => n.id === roving.active);
    e.preventDefault();
    roving.go(nodes[Math.max(0, Math.min(nodes.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)))].id);
  };
  return (
    <div className="strip">
      <button type="button" className="strip-back" aria-label="Back to the chain" data-tip="Back to the chain (Esc)" onClick={onBack}>← chain</button>
      <div className="strip-scroll">
        <div ref={world} role="group" aria-label="Chain" className="strip-map" onKeyDown={onKeyDown}>
          {nodes.map((n, i) => (
            <Fragment key={n.id}>
              {i > 0 && <span className={n.state === "todo" ? "strip-edge is-todo" : "strip-edge"} aria-hidden="true" />}
              <button
                ref={roving.ref(n.id)}
                type="button"
                tabIndex={roving.tabIndex(n.id)}
                className="strip-glyph"
                {...tip(accessibleName(names ? { ...n, id: displayName(n) } : n, n.kind === "gate" ? "gate" : "node"))}
                aria-current={n.id === viewing ? "step" : undefined}
                onFocus={() => roving.go(n.id)}
                onClick={() => onOpen?.(n.id)}
              >
                <span className={`map-glyph is-${n.kind} is-${tone(n)}${n.prob ? " is-prob" : ""}`} aria-hidden="true" />
              </button>
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}
