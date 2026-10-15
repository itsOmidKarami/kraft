import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import { ChevronLeft, ChevronRight } from "../icons";
import { DRAG_THRESHOLD } from "./camera";
import type { FitRule } from "./camera";
import { arcShapes, FOLD, L, layout, seamX, type ChainArc, type ChainNode, type Seam } from "./layout";
import { NodeGlyph } from "./NodeGlyph";
import { foldName, foldStages, rowSeams, stagesOf } from "./stages";
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
  /** With `fit`'s floor, a chain too wide to show whole opens centred on the node the run stands on (running, waiting or
   *  stopped), or on the last one. */
  focus?: "current" | "last";
  /** Px the docked pane takes on the right (rule F.6): the canvas ends where the pane starts, so nothing is drawn under it. */
  reserve?: number;
  /** Px an overlaid pane covers on the right: the current-node framing keeps clear of it (Kraft-gvfm2). */
  cover?: number;
  /** The editor's fit (Templates prototype `fit1`): see `FitRule`. */
  fit?: FitRule;
  /** Fold the stages the run has passed, and those past the next, to one pill each (`foldStages`); a click opens
   *  one in place for this page view. The item page's; an editor draws every node. */
  fold?: boolean;
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
const foldKey = (key: string) => `f:${key}`;

/** The chain canvas: a chain in one row (StageGraph.dc.html), node by node or with its stages folded. */
export function StageGraph({ name, nodes, selected, arcs = [], seams: chainSeams = [], opening = "fit", focus, reserve = 0, cover = 0, fit, fold = false, onDrag, onSelect, onOpen, onFocusNode, onEscape, onBackground, onSeam }: Props) {
  const [opened, setOpened] = useState<ReadonlySet<string>>(() => new Set());
  const row = useMemo(() => (fold ? foldStages(nodes, opened, selected) : nodes), [fold, nodes, opened, selected]);
  const lay = useMemo(() => layout(row), [row]);
  const seams = useMemo(() => rowSeams(nodes, row, chainSeams), [nodes, row, chainSeams]);
  const shapes = useMemo(() => arcShapes(lay, arcs), [lay, arcs]);
  // Where the run stands: the running node, else the one it stopped on (failed) or waits at (a gate).
  const cur = lay.items.find((i) => i.node.state === "current") ?? lay.items.find((i) => i.node.state === "failed" || i.node.state === "amber");
  const focusAt = (focus === "last" ? lay.items.at(-1) : focus === "current" ? cur : undefined)?.cx;
  // Past the current node, what the opening view tries to show whole: the next stage's end, else the current stage's, else the next column.
  const reach = useMemo(() => {
    if (focus !== "current" || !cur) return undefined;
    const right = (id: string) => lay.items.find((i) => i.node.id === id) ?? lay.folds.find((f) => f.fold.nodes.some((n) => n.id === id));
    const edge = (c?: { cx: number; w: number }) => (c ? [c.cx + c.w / 2] : []);
    const stages = stagesOf(nodes);
    const at = stages.findIndex((st) => st.nodes.some((n) => n.id === cur.node.id));
    const nextCol = [...lay.items, ...lay.folds].filter((c) => c.cx > cur.cx).sort((a, b) => a.cx - b.cx)[0];
    return [...edge(stages[at + 1] && right(stages[at + 1].nodes.at(-1)!.id)), ...edge(right(stages[at].nodes.at(-1)!.id)), ...edge(nextCol)];
  }, [focus, cur, lay, nodes]);
  const camera = useCamera({ canvas: "chain", world: lay, opening, cover, fit: fit && focusAt != null ? { ...fit, focus: focusAt, reach } : fit, current: cur && { cx: cur.cx, cy: L.CY } });

  // Keyboard stops in visual order: a seam at `at` sits before node `at`; a folded stage is one stop.
  const stops = useMemo(() => {
    const out: string[] = [];
    row.forEach((e, k) => {
      for (const s of seams) if (s.at === k) out.push(seamKey(s.at));
      out.push(e.kind === "fold" ? foldKey(e.key) : nodeKey(e.id));
    });
    for (const s of seams) if (s.at === row.length) out.push(seamKey(s.at));
    return out;
  }, [row, seams]);
  // Tab enters on the selected node, else the current one the camera opened on.
  const roving = useRoving(selected && nodeKey(selected), cur ? nodeKey(cur.node.id) : stops[0]);
  // A stage opened in place: the view stays put (it opens rightward from where its pill was) and focus goes to its
  // first node, the pill having gone.
  const landing = useRef<string | null>(null);
  const expand = (key: string, first: string) => {
    camera.hold();
    landing.current = nodeKey(first);
    setOpened((o) => new Set(o).add(key));
  };
  useEffect(() => {
    if (landing.current && stops.includes(landing.current)) roving.go(landing.current);
    landing.current = null;
  }, [stops]); // eslint-disable-line react-hooks/exhaustive-deps

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
  const { cam, view } = camera;
  // Columns the view cuts at either edge: not drawn, so no label is cut mid-word, and counted by the cues that slide
  // the chain to them (a folded stage by its nodes).
  const cut = (c: { cx: number; w: number }, side: "left" | "right") => !!view && (side === "left" ? cam.tx + (c.cx - c.w / 2) * cam.s < 0 : cam.tx + (c.cx + c.w / 2) * cam.s > view.w);
  const clipped = (c: { cx: number; w: number }) => cut(c, "left") || cut(c, "right");
  const edge = (side: "left" | "right") => {
    const out = [...lay.items.map((i) => ({ ...i, n: 1 })), ...lay.folds.map((f) => ({ ...f, n: f.fold.nodes.length }))].filter((c) => cut(c, side)).sort((a, b) => a.cx - b.cx);
    const far = out[side === "left" ? 0 : out.length - 1];
    // Up to three quarters of the view, no further than brings the far one in with 24px to spare.
    const dx = view && far ? Math.min(view.w * 0.75, side === "left" ? 24 - (cam.tx + (far.cx - far.w / 2) * cam.s) : cam.tx + (far.cx + far.w / 2) * cam.s - (view.w - 24)) : 0;
    return { n: out.reduce((t, c) => t + c.n, 0), dx: side === "left" ? dx : -dx };
  };
  const before = edge("left"), after = edge("right");
  // Above the row, so a cue never covers the node the view cuts through.
  const cueTop = Math.max(8, cam.ty + (L.CY - L.BOX / 2) * cam.s - 40);
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
    <div role="group" aria-label={name} className="canvas" style={{ right: reserve }} data-pan {...camera.bind} ref={bindRef} onClick={onClick} onKeyDown={onKeyDown} onClickCapture={(e) => {
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
              className={`graph-node${bold ? " is-bold" : ""}${clipped({ cx, w }) ? " is-clipped" : ""}${n.state === "todo" ? " is-todo" : ""}${n.state === "ghost" ? " is-ghost" : ""}${n.mark === "add" ? (n.prob ? " is-bad" : " is-add") : ""}${n.pending ? " is-pending" : ""}${n.faded ? " is-faded" : ""}${drag?.id === n.id ? " is-dragging" : ""}`}
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
        {lay.folds.map(({ fold: f, w, cx }) => {
          const key = foldKey(f.key);
          const gate = f.nodes.at(-1)!;
          return (
            <button
              key={key}
              ref={roving.ref(key)}
              type="button"
              tabIndex={roving.tabIndex(key)}
              {...tip(foldName(f), f.nodes.map((n) => n.id).join(" → "))}
              className={`graph-node graph-fold${f.passed ? "" : " is-todo"}${clipped({ cx, w }) ? " is-clipped" : ""}`}
              style={{ left: cx - w / 2, top: L.CY - FOLD.H / 2, width: w }}
              onFocus={() => { roving.go(key); camera.reveal({ x0: cx - w / 2, x1: cx + w / 2, y0: L.CY - FOLD.H / 2, y1: L.CY + FOLD.H / 2 + 24 }); }}
              onClick={() => expand(f.key, f.nodes[0].id)}
            >
              <span className="fold-pill" style={{ height: FOLD.H }}>
                <NodeGlyph kind={gate.kind} state={f.passed ? "done" : "todo"} size="sm" />
                <span>{f.words}</span>
                {f.passed && <span className="fold-done" aria-hidden="true">✓</span>}
              </span>
              <span className="graph-meta">{f.nodes.length} {f.nodes.length === 1 ? "node" : "nodes"}</span>
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
              onClick={(e) => onSeam?.(s.node, e.currentTarget)}
            >
              <span aria-hidden="true">+</span>
            </button>
          );
        })}
      </div>
      {before.n > 0 && <button type="button" className="canvas-more is-left" style={{ top: cueTop }} aria-label={`${before.n} earlier ${before.n === 1 ? "node" : "nodes"} off-screen`} onPointerDown={(e) => e.stopPropagation()} onClick={() => camera.pan(before.dx)}><ChevronLeft size={12} aria-hidden="true" />+{before.n}</button>}
      {after.n > 0 && <button type="button" className="canvas-more is-right" style={{ top: cueTop }} aria-label={`${after.n} more ${after.n === 1 ? "node" : "nodes"} off-screen`} onPointerDown={(e) => e.stopPropagation()} onClick={() => camera.pan(after.dx)}>+{after.n}<ChevronRight size={12} aria-hidden="true" /></button>}
      <div className="canvas-zoom">
        <ZoomControls scale={cam.s} mode={camera.mode} onIn={camera.zoomIn} onOut={camera.zoomOut} onReset={camera.reset} onFit={camera.fit} onCurrent={camera.toCurrent} fitLabel="Fit the chain" />
      </div>
    </div>
  );
}
