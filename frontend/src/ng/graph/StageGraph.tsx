import { useMemo, type KeyboardEvent, type MouseEvent } from "react";
import { arcShapes, L, layout, seamX, type ChainArc, type ChainNode, type Seam } from "./layout";
import { NodeGlyph } from "./NodeGlyph";
import { accessibleName, breakable } from "./types";
import { useCamera } from "./useCamera";
import { useRoving } from "./useRoving";
import { ZoomControls } from "./ZoomControls";
import "./graph.css";

type Props = {
  /** The chain's name: the canvas group's accessible name. */
  name: string;
  nodes: ChainNode[];
  selected?: string;
  arcs?: ChainArc[];
  seams?: Seam[];
  opening?: "fit" | "current";
  /** Px the docked pane takes on the right (rule F.6). */
  reserve?: number;
  onSelect?: (id: string) => void;
  onOpen?: (id: string) => void;
  onFocusNode?: (id: string) => void;
  onEscape?: () => void;
  onBackground?: () => void;
  onSeam?: (at: number) => void;
};

const nodeKey = (id: string) => `n:${id}`;
const seamKey = (at: number) => `s:${at}`;

/** The chain canvas: every node of a chain in one row (StageGraph.dc.html). */
export function StageGraph({ name, nodes, selected, arcs = [], seams = [], opening = "fit", reserve = 0, onSelect, onOpen, onFocusNode, onEscape, onBackground, onSeam }: Props) {
  const lay = useMemo(() => layout(nodes), [nodes]);
  const shapes = useMemo(() => arcShapes(lay, arcs), [lay, arcs]);
  const cur = lay.items.find((i) => i.node.state === "current");
  const camera = useCamera({ canvas: "chain", world: lay, opening, reserve, current: cur && { cx: cur.cx, cy: L.CY } });

  // Keyboard stops in visual order: a seam at `at` sits before node `at`.
  const stops = useMemo(() => {
    const out: string[] = [];
    lay.items.forEach((it, k) => {
      for (const s of seams) if (s.at === k) out.push(seamKey(s.at));
      out.push(nodeKey(it.node.id));
    });
    for (const s of seams) if (s.at === lay.items.length) out.push(seamKey(s.at));
    return out;
  }, [lay, seams]);
  // Tab enters on the selected node, else the current one the camera opened on.
  const roving = useRoving(selected && nodeKey(selected), cur ? nodeKey(cur.node.id) : stops[0]);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onEscape?.();
      return;
    }
    if (!(e.target as Element).closest(".graph-node, .seam")) return;
    const i = stops.indexOf(roving.active ?? "");
    const id = roving.active?.startsWith("n:") ? roving.active.slice(2) : null;
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      roving.go(stops[Math.max(0, Math.min(stops.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)))]);
    } else if (e.key === "Enter" && id) {
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) onFocusNode?.(id);
      else onOpen?.(id);
    }
  };
  const onClick = (e: MouseEvent) => {
    if (!(e.target as Element).closest("button")) onBackground?.();
  };
  const { cam } = camera;

  return (
    <div role="group" aria-label={name} className="canvas" data-pan {...camera.bind} onClick={onClick} onKeyDown={onKeyDown}>
      <div className="canvas-world" style={{ width: lay.W, height: lay.H, transform: `translate(${cam.tx}px, ${cam.ty}px) scale(${cam.s})` }}>
        <svg className="canvas-svg" width={lay.W} height={lay.H} aria-hidden="true">
          {lay.edges.map((e, i) => <path key={`e${i}`} d={e.d} className={e.todo ? "edge is-todo" : "edge"} />)}
          {lay.dots.map((d, i) => <circle key={`d${i}`} cx={d.x} cy={d.y} r={3} className={d.todo ? "dot is-todo" : "dot"} />)}
          {shapes.map((a) => (
            <g key={a.key} className={`arc tone-${a.tone}`}>
              <path d={a.d} className={a.dashed ? "is-dashed" : undefined} />
              <path d={a.arrow} />
            </g>
          ))}
        </svg>
        {shapes.map((a) => a.label && <span key={`${a.key}l`} className={`arc-label tone-${a.tone}`} style={{ left: a.label.x, top: a.label.y }}>{a.label.text}</span>)}
        <span className="mark-start" style={{ left: 26, top: L.CY - 10 }} aria-hidden="true">▶</span>
        <span className="mark-end" style={{ left: lay.W - 44, top: L.CY - 8 }} aria-hidden="true" />
        {lay.items.map(({ node: n, w, cx }) => {
          const key = nodeKey(n.id);
          const bold = n.id === selected || n.state === "current";
          return (
            <button
              key={key}
              ref={roving.ref(key)}
              type="button"
              tabIndex={roving.tabIndex(key)}
              aria-label={accessibleName(n, n.kind === "gate" ? "gate" : "node")}
              aria-pressed={n.id === selected}
              className={`graph-node${bold ? " is-bold" : ""}${n.state === "todo" ? " is-todo" : ""}${n.state === "ghost" ? " is-ghost" : ""}${n.mark === "add" ? (n.prob ? " is-bad" : " is-add") : ""}`}
              style={{ left: cx - w / 2, top: L.CY - L.BOX / 2, width: w }}
              onFocus={() => { roving.go(key); camera.reveal({ x0: cx - w / 2, x1: cx + w / 2, y0: L.CY - L.BOX / 2, y1: L.CY + L.BOX / 2 + 40 }); }}
              onClick={() => onSelect?.(n.id)}
              onDoubleClick={() => onFocusNode?.(n.id)}
            >
              <NodeGlyph {...n} size="lg" sel={n.id === selected} />
              <span className="graph-label" style={{ maxWidth: w - 8 }}>{breakable(n.label ?? n.id)}</span>
              {n.meta && <span className={`graph-meta tone-${n.metaTone ?? "muted"}`}>{n.meta}</span>}
              {n.sub && <span className="graph-sub"><span className={`graph-sub-dot tone-${n.subTone ?? "amber"}`} />{n.sub}</span>}
            </button>
          );
        })}
        {seams.map((s) => {
          const x = seamX(lay, s.at);
          if (x == null) return null;
          const key = seamKey(s.at);
          return (
            <button
              key={key}
              ref={roving.ref(key)}
              type="button"
              tabIndex={roving.tabIndex(key)}
              aria-label={s.title ?? "Add a node or gate here"}
              title={s.title ?? "Add a node or gate here"}
              className={`seam${s.open ? " is-open" : ""}${s.always ? " is-always" : ""}`}
              style={{ left: x - 10, top: L.CY - 10 }}
              onFocus={() => { roving.go(key); camera.reveal({ x0: x - 10, x1: x + 10, y0: L.CY - 10, y1: L.CY + 10 }); }}
              onClick={() => onSeam?.(s.at)}
            >
              <span aria-hidden="true">+</span>
            </button>
          );
        })}
      </div>
      <div className="canvas-zoom" style={{ right: reserve + 12 }}>
        <ZoomControls scale={cam.s} mode={camera.mode} onIn={camera.zoomIn} onOut={camera.zoomOut} onReset={camera.reset} onFit={camera.fit} onCurrent={camera.toCurrent} fitLabel="Fit the chain" />
      </div>
    </div>
  );
}
