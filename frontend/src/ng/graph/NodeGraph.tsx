import { useMemo, type KeyboardEvent, type MouseEvent } from "react";
import { NodeGlyph } from "./NodeGlyph";
import { G, loopArc, nodeEdges, nodeLayout, sideBranch, type NodeStep } from "./nodeLayout";
import { accessibleName, breakable, type GraphItem } from "./types";
import { useCamera } from "./useCamera";
import { useRoving } from "./useRoving";
import { ZoomControls } from "./ZoomControls";
import "./graph.css";
import { tip } from "../ui/Tooltip";

/** A step, or a task in it. The escalation task's step is `escalation`. */
export type NodeSel = { step: string; task?: string };

type Props = {
  /** The node's id: the canvas group's accessible name. */
  name: string;
  steps: NodeStep[];
  selected?: NodeSel;
  /** The escalation task, on a dashed branch off the end. */
  side?: GraphItem;
  loop?: { tone?: "idle" | "active" | "red"; label?: string };
  /** The node's own on_failure, as one footer line. */
  onFailure?: string;
  /** A "+" seam after the last step. */
  seamAfter?: boolean;
  reserve?: number;
  onSelect?: (s: NodeSel) => void;
  onOpen?: (s: NodeSel) => void;
  /** ⌘Enter or double-click: expand the pane on it. */
  onExpand?: (s: NodeSel) => void;
  onEscape?: () => void;
  onBackground?: () => void;
  /** The slot's or seam's own button comes last, for a menu anchored to it. */
  onSlot?: (step: string, el: HTMLElement) => void;
  onSeam?: (where: "below" | "before" | "after", at: string | number, el: HTMLElement) => void;
};

const taskKey = (s: string, t: string) => `t:${s}/${t}`;
const labelKey = (s: string) => `l:${s}`;
const parse = (key: string): NodeSel => {
  const [step, task] = key.slice(2).split("/");
  return key.startsWith("t:") ? { step, task } : { step };
};

/** A node's inside: steps in order, parallel tasks as rows (NodeGraph.dc.html). */
export function NodeGraph({ name, steps, selected, side, loop, onFailure, seamAfter, reserve = 0, onSelect, onOpen, onExpand, onEscape, onBackground, onSlot, onSeam }: Props) {
  const lay = useMemo(() => nodeLayout(steps, { side: !!side, loop: !!loop, footer: !!onFailure }), [steps, side, loop, onFailure]);
  const { edges, dots } = useMemo(() => nodeEdges(steps, lay), [steps, lay]);
  const camera = useCamera({ canvas: "node", world: lay, opening: "fit", reserve });
  const selKey = selected && (selected.task ? taskKey(selected.step, selected.task) : labelKey(selected.step));
  const firstStep = steps[0];
  const roving = useRoving(selKey, firstStep && (firstStep.tasks[0] ? taskKey(firstStep.id, firstStep.tasks[0].id) : labelKey(firstStep.id)));

  // ←/→ to the neighbouring step at the same row (clamped), ↑/↓ within a step, ↑ from the top task to the step's label.
  const move = (key: string, dir: string): string | undefined => {
    const { step, task } = parse(key);
    const k = steps.findIndex((s) => s.id === step);
    if (k < 0) return;
    const i = task ? steps[k].tasks.findIndex((t) => t.id === task) : -1;
    const at = (kk: number, row: number) => {
      const st = steps[kk];
      if (!st) return;
      if (row < 0 || !st.tasks.length) return labelKey(st.id);
      return taskKey(st.id, st.tasks[Math.min(row, st.tasks.length - 1)].id);
    };
    if (dir === "ArrowRight") return at(k + 1, i);
    if (dir === "ArrowLeft") return at(k - 1, i);
    if (dir === "ArrowUp") return at(k, i - 1);
    if (dir === "ArrowDown") return at(k, i + 1);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); onEscape?.(); return; }
    const key = roving.active;
    if (!key || !(e.target as Element).closest(".graph-node, .step-label")) return;
    if (e.key.startsWith("Arrow")) { e.preventDefault(); roving.go(move(key, e.key)); }
    else if (e.key === "Enter") { e.preventDefault(); (e.metaKey || e.ctrlKey ? onExpand : onOpen)?.(parse(key)); }
  };
  const onClick = (e: MouseEvent) => {
    if (!(e.target as Element).closest("button, .step-frame")) onBackground?.();
  };
  const { cam } = camera;
  const loopShape = loop && loopArc(lay, loop.label);
  const sb = side && sideBranch(lay);
  const isSel = (s: NodeSel) => selected?.step === s.step && selected?.task === s.task;
  const last = lay.cols[lay.cols.length - 1];

  const seam = (key: string, x: number, y: number, title: string, fire: (el: HTMLElement) => void) => (
    <button key={key} type="button" className="seam is-node" {...tip(title)} style={{ left: x - 10, top: y - 10 }} onClick={(e) => fire(e.currentTarget)}><span aria-hidden="true">+</span></button>
  );
  const taskButton = (s: NodeSel, t: GraphItem, x: number, y: number, kind: string, extra = "") => {
    const key = taskKey(s.step, t.id), sel = isSel(s);
    return (
      <button
        key={key}
        ref={roving.ref(key)}
        type="button"
        tabIndex={roving.tabIndex(key)}
        aria-label={accessibleName(t, kind)}
        aria-pressed={sel}
        className={`graph-node is-task${sel || t.state === "current" ? " is-bold" : ""}${t.state === "todo" ? " is-todo" : ""}${t.state === "esc" || t.state === "amber" ? " is-warn" : ""}${t.pending ? " is-pending" : ""}${extra}`}
        style={{ left: x - G.COL / 2, top: y - G.BOX / 2, width: G.COL }}
        onFocus={() => { roving.go(key); camera.reveal({ x0: x - G.COL / 2, x1: x + G.COL / 2, y0: y - G.BOX / 2, y1: y + G.BOX / 2 + 36 }); }}
        onClick={() => onSelect?.(s)}
        onDoubleClick={() => onExpand?.(s)}
      >
        <NodeGlyph {...t} size="md" sel={sel} />
        <span className="graph-label" style={{ maxWidth: G.COL - 6 }}>{breakable(t.label ?? t.id)}</span>
        {t.meta && <span className={`graph-meta tone-${t.metaTone ?? "muted"}`}>{t.meta}</span>}
      </button>
    );
  };

  return (
    <div role="group" aria-label={name} className="canvas" data-pan {...camera.bind} onClick={onClick} onKeyDown={onKeyDown}>
      <div className="canvas-world" style={{ width: lay.W, height: lay.H, transform: `translate(${cam.tx}px, ${cam.ty}px) scale(${cam.s})` }}>
        {steps.map((st, k) => {
          const f = lay.cols[k].frame;
          const cls = `step-frame${selected?.step === st.id && !selected.task ? " is-sel" : ""}${st.mark ? ` mark-${st.mark}` : st.prob ? " is-prob" : ""}`;
          return f && <div key={`f${st.id}`} aria-hidden="true" className={cls} style={{ left: f.x, top: f.y, width: f.w, height: f.h }} onClick={() => onSelect?.({ step: st.id })} />;
        })}
        <svg className="canvas-svg" width={lay.W} height={lay.H} aria-hidden="true">
          {edges.map((e) => <path key={e.key} d={e.d} className={e.todo ? "edge is-todo" : "edge"} />)}
          {dots.map((d, i) => <circle key={`j${i}`} cx={d.x} cy={d.y} r={3} className="dot" />)}
          {sb && <path d={sb.d} className="edge is-side" />}
          {loopShape && (
            <g className={`arc tone-${loop.tone ?? "idle"}`}>
              <path d={loopShape.d} className="is-dashed" />
              <path d={loopShape.arrow} />
            </g>
          )}
        </svg>
        {loopShape?.label && <span className={`arc-label tone-${loop?.tone ?? "idle"}`} style={{ left: loopShape.label.x, top: loopShape.label.y }}>{loopShape.label.text}</span>}
        <span className="mark-start is-node" style={{ left: G.startX, top: lay.TY - 9 }} aria-hidden="true">▶</span>
        <span className="mark-end" style={{ left: lay.endX - 8, top: lay.TY - 8 }} aria-hidden="true" />
        {steps.map((st, k) => {
          const { cx, ys, labelY } = lay.cols[k];
          const key = labelKey(st.id), sel = selected?.step === st.id && !selected.task;
          return [
            <button
              key={key}
              ref={roving.ref(key)}
              type="button"
              tabIndex={roving.tabIndex(key)}
              // A lone step draws no label (a blank one): its id names it, and the tooltip says it.
              {...(st.label?.trim() === "" ? tip(`${st.id}, step`) : { "aria-label": `${st.label ?? st.id}, step` })}
              aria-pressed={sel}
              className={`step-label${sel ? " is-sel" : ""}${st.mark ? ` mark-${st.mark}` : ""}`}
              style={{ left: cx - G.COL / 2, top: labelY, width: G.COL }}
              onFocus={() => { roving.go(key); camera.reveal({ x0: cx - G.COL / 2, x1: cx + G.COL / 2, y0: labelY, y1: labelY + 20 }); }}
              onClick={() => onSelect?.({ step: st.id })}
              onDoubleClick={() => onExpand?.({ step: st.id })}
            >
              {st.label ?? st.id}
              {st.prob && <span className="step-prob" aria-hidden="true"> !</span>}
            </button>,
            ...st.tasks.map((t, i) => taskButton({ step: st.id, task: t.id }, t, cx, ys[i], `${t.taskKind ?? "agent"} task`)),
            !st.tasks.length && st.slot && (
              <button key={`slot${st.id}`} type="button" className="graph-node is-task is-slot" style={{ left: cx - G.COL / 2, top: ys[0] - G.BOX / 2, width: G.COL }} onClick={(e) => onSlot?.(st.id, e.currentTarget)}>
                <NodeGlyph kind="slot" size="md" />
                <span className="graph-label">{st.slot.label ?? "add a task"}</span>
              </button>
            ),
            st.seamBelow && seam(`sb${st.id}`, cx, ys[ys.length - 1] + G.BOX / 2 + 56, "Add a parallel task", (el) => onSeam?.("below", st.id, el)),
            st.seamBefore && seam(`sf${st.id}`, k === 0 ? (G.startX + 18 + cx - G.BOX / 2) / 2 : (lay.cols[k - 1].cx + cx) / 2, lay.TY, "Add a step here", (el) => onSeam?.("before", k, el)),
          ];
        })}
        {sb && side && taskButton({ step: "escalation", task: side.id }, { ...side, state: side.state ?? "esc" }, sb.x, sb.y, "escalation task", " is-side")}
        {onFailure && <span className="node-footer" style={{ left: G.startX, top: lay.footerY }}>on failure · {onFailure}</span>}
        {seamAfter && seam("after", last ? (last.cx + G.BOX / 2 + lay.endX) / 2 : (G.startX + 18 + lay.endX) / 2, lay.TY, "Add a step here", (el) => onSeam?.("after", steps.length, el))}
      </div>
      <div className="canvas-zoom" style={{ right: reserve + 12 }}>
        <ZoomControls scale={cam.s} mode={camera.mode} onIn={camera.zoomIn} onOut={camera.zoomOut} onReset={camera.reset} onFit={camera.fit} fitLabel="Fit the node" />
      </div>
    </div>
  );
}
