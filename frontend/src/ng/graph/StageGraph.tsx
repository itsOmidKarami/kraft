import { useCallback, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import { DRAG_THRESHOLD } from "./camera";
import type { FitRule } from "./camera";
import { arcShapes, L, layout, seamX, type ChainArc, type ChainNode, type Seam } from "./layout";
import { NodeGlyph } from "./NodeGlyph";
import { accessibleName, breakable } from "./types";
import { useCamera } from "./useCamera";
import { useRoving } from "./useRoving";
import { ZoomControls } from "./ZoomControls";
import "./graph.css";
import { tip } from "../ui/Tooltip";

type Props = {
  /** The chain's name: the canvas group's accessible name. */
  name: string;
  nodes: ChainNode[];
  selected?: string;
  arcs?: ChainArc[];
  seams?: Seam[];
  opening?: "fit" | "current";
  /** With `fit`'s floor, a chain too wide to show whole opens centred on the node the run stopped on, or the last one. */
  focus?: "stopped" | "last";
  /** Px the docked pane takes on the right (rule F.6). */
  reserve?: number;
  /** Px an overlaid pane covers on the right: the current-node framing keeps clear of it (Kraft-gvfm2). */
  cover?: number;
  /** The editor's fit (Templates prototype `fit1`): see `FitRule`. */
  fit?: FitRule;
  onSelect?: (id: string) => void;
  onOpen?: (id: string) => void;
  onFocusNode?: (id: string) => void;
  onEscape?: () => void;
  onBackground?: () => void;
  /** Drag to reorder (the editor's, Decisions §9 Reorder): `to` is the index in
   *  the list without the dragged node, as a move op takes it. */
  onDrag?: { over: (id: string, to: number) => void; drop: (id: string, to: number) => void; end: () => void };
  /** The seam's own button, for a menu anchored to it. */
  onSeam?: (at: number, el: HTMLElement) => void;
};

const nodeKey = (id: string) => `n:${id}`;
const seamKey = (at: number) => `s:${at}`;

/** The chain canvas: every node of a chain in one row (StageGraph.dc.html). */
export function StageGraph({ name, nodes, selected, arcs = [], seams = [], opening = "fit", focus, reserve = 0, cover = 0, fit, onDrag, onSelect, onOpen, onFocusNode, onEscape, onBackground, onSeam }: Props) {
  const lay = useMemo(() => layout(nodes), [nodes]);
  const shapes = useMemo(() => arcShapes(lay, arcs), [lay, arcs]);
  // Where the run stands: the running node, else the one it stopped on (failed) or waits at (a gate).
  const cur = lay.items.find((i) => i.node.state === "current") ?? lay.items.find((i) => i.node.state === "failed" || i.node.state === "amber");
  const focusAt = (focus === "last" ? lay.items.at(-1) : focus === "stopped" ? cur : undefined)?.cx;
  const camera = useCamera({ canvas: "chain", world: lay, opening, reserve, cover, fit: fit && focusAt != null ? { ...fit, focus: focusAt } : fit, current: cur && { cx: cur.cx, cy: L.CY } });

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
  const [drag, setDrag] = useState<{ id: string; to: number } | null>(null);
  const dragged = useRef(false);
  const viewport = useRef<HTMLElement | null>(null);
  const setCamEl = camera.bind.ref;
  const bindRef = useCallback((el: HTMLDivElement | null) => {
    setCamEl(el);
    viewport.current = el;
  }, [setCamEl]);
  /** A node pressed and moved past the camera's threshold lifts; release drops it. */
  const startDrag = (e: PointerEvent<HTMLButtonElement>, id: string) => {
    if (!onDrag || e.button !== 0) return;
    e.stopPropagation();
    const x0 = e.clientX, y0 = e.clientY;
    const others = lay.items.filter((i) => i.node.id !== id);
    let at = -1;
    dragged.current = false;
    const move = (ev: globalThis.PointerEvent) => {
      if (!dragged.current && Math.hypot(ev.clientX - x0, ev.clientY - y0) <= DRAG_THRESHOLD) return;
      dragged.current = true;
      const rect = viewport.current!.getBoundingClientRect();
      const wx = (ev.clientX - rect.left - cam.tx) / cam.s;
      const to = others.filter((o) => o.cx < wx).length;
      if (to !== at) {
        at = to;
        setDrag({ id, to });
        onDrag.over(id, to);
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setDrag(null);
      if (dragged.current && at >= 0) onDrag.drop(id, at);
      onDrag.end();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const dropX = drag ? (() => {
    const others = lay.items.filter((i) => i.node.id !== drag.id);
    const a = others[drag.to - 1], b = others[drag.to];
    return a && b ? (a.cx + a.w / 2 + b.cx - b.w / 2) / 2 : a ? a.cx + a.w / 2 + 10 : b ? b.cx - b.w / 2 - 10 : L.PAD;
  })() : null;

  return (
    <div role="group" aria-label={name} className="canvas" data-pan {...camera.bind} ref={bindRef} onClick={onClick} onKeyDown={onKeyDown} onClickCapture={(e) => {
      // A drag is never a click (W3 rule B.2), for a node drag as for a pan.
      if (dragged.current) { e.stopPropagation(); dragged.current = false; return; }
      camera.bind.onClickCapture(e);
    }}>
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
              className={`graph-node${bold ? " is-bold" : ""}${n.state === "todo" ? " is-todo" : ""}${n.state === "ghost" ? " is-ghost" : ""}${n.mark === "add" ? (n.prob ? " is-bad" : " is-add") : ""}${n.pending ? " is-pending" : ""}${n.faded ? " is-faded" : ""}${drag?.id === n.id ? " is-dragging" : ""}`}
              style={{ left: cx - w / 2, top: L.CY - L.BOX / 2, width: w }}
              onFocus={() => { roving.go(key); camera.reveal({ x0: cx - w / 2, x1: cx + w / 2, y0: L.CY - L.BOX / 2, y1: L.CY + L.BOX / 2 + 40 }); }}
              onClick={() => onSelect?.(n.id)}
              onDoubleClick={() => onFocusNode?.(n.id)}
              onPointerDown={onDrag ? (e) => startDrag(e, n.id) : undefined}
            >
              <NodeGlyph {...n} size="lg" sel={n.id === selected} />
              <span className="graph-label" style={{ maxWidth: w - 8 }}>{breakable(n.label ?? n.id)}</span>
              {n.meta && <span className={`graph-meta tone-${n.metaTone ?? "muted"}`}>{n.meta}</span>}
              {n.sub && <span className="graph-sub"><span className={`graph-sub-dot tone-${n.subTone ?? "amber"}`} />{n.sub}</span>}
            </button>
          );
        })}
        {dropX !== null && <span className="drop-slot" style={{ left: dropX - 1, top: L.CY - 30 }} aria-hidden="true" />}
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
              {...tip(s.title ?? "Add a node or gate here")}
              className={`seam${s.open ? " is-open" : ""}${s.always ? " is-always" : ""}`}
              style={{ left: x - 10, top: L.CY - 10 }}
              onFocus={() => { roving.go(key); camera.reveal({ x0: x - 10, x1: x + 10, y0: L.CY - 10, y1: L.CY + 10 }); }}
              onClick={(e) => onSeam?.(s.at, e.currentTarget)}
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
