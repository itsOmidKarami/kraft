import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { displayName } from "../board/rowText";
import type { ChainNode } from "./layout";
import { tone } from "./Minimap";
import { chainNodeName } from "./types";
import { useRoving } from "./useRoving";
import "./graph.css";
import { tip, hint } from "../ui/Tooltip";

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
  // A group (slices) is one glyph that opens to its body: open by default while a node inside it is in view.
  const [forced, setForced] = useState<Record<string, boolean>>({});
  const isOpen = (n: ChainNode) => n.kind === "group" && (forced[n.id] ?? viewing.startsWith(`${n.id}.`));
  // The glyphs on show, in order: ← and → walk these.
  const shown = nodes.flatMap((n) => (isOpen(n) ? [n, ...(n.body ?? [])] : [n]));
  const roving = useRoving(viewing, nodes[0]?.id);
  // A strip wider than its bar scrolls to the node in view.
  const world = useRef<HTMLDivElement>(null);
  useEffect(() => {
    world.current?.querySelector("[aria-current]")?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [viewing]);
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = shown.findIndex((n) => n.id === roving.active);
    e.preventDefault();
    roving.go(shown[Math.max(0, Math.min(shown.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)))].id);
  };
  return (
    <div className="strip">
      <button type="button" className="strip-back" aria-label="Back to the chain" {...hint("Back to the chain (Esc)")} onClick={onBack}>← chain</button>
      <div className="strip-scroll">
        <div ref={world} role="group" aria-label="Chain" className="strip-map" onKeyDown={onKeyDown}>
          {nodes.map((n, i) => {
            const group = n.kind === "group";
            const open = isOpen(n);
            const glyph = (g: ChainNode, body = false) => (
              <button
                key={g.id}
                ref={roving.ref(g.id)}
                type="button"
                tabIndex={roving.tabIndex(g.id)}
                className="strip-glyph"
                {...tip(chainNodeName(g, names ? displayName(g) : g.id))}
                aria-current={g.id === viewing ? "step" : undefined}
                aria-expanded={g.kind === "group" ? isOpen(g) : undefined}
                onFocus={() => roving.go(g.id)}
                onClick={() => (g.kind === "group" ? setForced({ ...forced, [g.id]: !isOpen(g) }) : onOpen?.(g.id))}
              >
                <span className={`map-glyph is-${g.kind} is-${tone(g)}${g.prob ? " is-prob" : ""}${body ? " is-body" : ""}`} aria-hidden="true" />
              </button>
            );
            return (
              <Fragment key={n.id}>
                {i > 0 && <span className={n.state === "todo" ? "strip-edge is-todo" : "strip-edge"} aria-hidden="true" />}
                {glyph(n)}
                {group && open && <span className="strip-body" role="group" aria-label={`${n.id} body`}>{(n.body ?? []).map((b) => glyph(b, true))}</span>}
              </Fragment>
            );
          })}
        </div>
      </div>
    </div>
  );
}
