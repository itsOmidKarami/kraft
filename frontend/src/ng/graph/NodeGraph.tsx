import { useEffect, useMemo, useRef, type CSSProperties, type KeyboardEvent, type MouseEvent } from "react";
import { NodeGlyph } from "./NodeGlyph";
import { G, loopArc, loopSlots, nodeEdges, nodeLayout, sideBranch, type NodeStep } from "./nodeLayout";
import { RoundPicker } from "./RoundPicker";
import { chipKey, ScopeFrame } from "./ScopeFrame";
import { frameHeight, frameWidth, type ScopesView } from "../item/scopeView";
import type { Rounds } from "../item/nodeGraph";
import { accessibleName, breakable, type GraphItem } from "./types";
import { useCamera } from "./useCamera";
import { useExpand, useReducedMotion, useSettled } from "./useExpand";
import { useRoving } from "./useRoving";
import { ZoomControls } from "./ZoomControls";
import "./graph.css";
import { tip } from "../ui/Tooltip";

/** A step, or a task in it, or one of an open task's scopes (its `key`). The escalation task's step is
 *  `escalation`, a fix loop's repair and judge `fix_loop`. */
export type NodeSel = { step: string; task?: string; scope?: string };

type Props = {
  /** The node's id: the canvas group's accessible name. */
  name: string;
  steps: NodeStep[];
  selected?: NodeSel;
  /** The escalation task, on a dashed branch off the end. */
  side?: GraphItem;
  loop?: { tone?: "idle" | "active" | "red"; label?: string; /** The repair tasks, then the judge, drawn on the arc. */ tasks?: GraphItem[] };
  /** The fix-loop rounds, with the one the canvas shows; picking one (undefined: the newest) is `onRound`. */
  rounds?: Rounds;
  onRound?: (round: number | undefined) => void;
  /** A task drawn open, as a frame of its repositories and scopes in place of its box. */
  expand?: { step: string; task: string; view: ScopesView; scope?: string };
  /** A scope of the open task picked. */
  onScope?: (key: string) => void;
  /** The open task's own close. */
  onCollapse?: () => void;
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
  const [step, task, ...rest] = key.slice(2).split("/");
  return key.startsWith("c:") ? { step, task, scope: rest.join("/") } : key.startsWith("t:") ? { step, task } : { step };
};

/** A node's inside: steps in order, parallel tasks as rows (NodeGraph.dc.html). */
export function NodeGraph({ name, steps, selected, side, loop, rounds, onRound, expand, onScope, onCollapse, onFailure, seamAfter, reserve = 0, onSelect, onOpen, onExpand, onEscape, onBackground, onSlot, onSeam }: Props) {
  const loopTasks = loop?.tasks ?? [];
  // The frame stays in the page while it closes, which takes the view it was drawn from with it.
  const kept = useRef(expand);
  if (expand) kept.current = expand;
  const calm = useReducedMotion();
  const phase = useExpand(!!expand, calm);
  const shown = expand ?? (phase.mounted ? kept.current : undefined);
  const openStep = shown ? steps.findIndex((st) => st.id === shown.step) : -1;
  const openRow = shown && openStep >= 0 ? steps[openStep].tasks.findIndex((t) => t.id === shown.task) : -1;
  // The frame never narrows while it stays open on one round: a chip that finishes reads shorter than it did running,
  // and the canvas would otherwise move every step after it each time a scope ends.
  const held = useRef({ key: "", w: 0 });
  if (shown) {
    const key = `${shown.step}/${shown.task}/${shown.view.round}`;
    held.current = { key, w: Math.max(key === held.current.key ? held.current.w : 0, frameWidth(shown.view)) };
  }
  const open = shown && openRow >= 0 ? { step: openStep, row: openRow, w: held.current.w, h: frameHeight(shown.view) } : undefined;
  const laid = { side: !!side, loop: !!loop, loopTasks: loopTasks.length > 0, footer: !!onFailure };
  const closed = useMemo(() => nodeLayout(steps, laid), [steps, side, loop, loopTasks.length, onFailure]); // eslint-disable-line react-hooks/exhaustive-deps
  const wide = useMemo(() => (open ? nodeLayout(steps, { ...laid, expand: open }) : closed), [closed, open?.step, open?.row, open?.w, open?.h]); // eslint-disable-line react-hooks/exhaustive-deps
  const lay = phase.layout && open ? wide : closed;
  const { edges, dots } = useMemo(() => nodeEdges(steps, lay), [steps, lay]);
  // The camera measures the world after it has moved, and keeps clear of the zoom and round controls at the foot.
  const settledW = useSettled(lay.W, phase.glide), settledH = useSettled(lay.H, phase.glide);
  const camera = useCamera({ canvas: "node", world: { W: settledW, H: settledH }, opening: "fit", reserve, clearBottom: 56 });
  const selKey = selected && (selected.scope && selected.task ? chipKey(selected.step, selected.task, selected.scope) : selected.task ? taskKey(selected.step, selected.task) : labelKey(selected.step));
  const firstStep = steps[0];
  const roving = useRoving(selKey, firstStep && (firstStep.tasks[0] ? taskKey(firstStep.id, firstStep.tasks[0].id) : labelKey(firstStep.id)));

  // An open task's box hides behind its frame, and the frame goes again: the focus follows each, not left on what left.
  const inside = useRef(false);
  // The hand-off must not pan the camera, which is still settling: a chip pressed just after would move under the pointer.
  const quiet = useRef(false);
  useEffect(() => {
    if (phase.layout && open && shown && document.activeElement?.classList.contains("is-away")) {
      quiet.current = true;
      roving.go(taskKey(shown.step, shown.task));
      quiet.current = false;
    }
  }, [phase.layout]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const was = kept.current;
    if (!phase.mounted && inside.current && document.activeElement === document.body && was) roving.go(taskKey(was.step, was.task));
  }, [phase.mounted]); // eslint-disable-line react-hooks/exhaustive-deps

  // ←/→ to the neighbouring step at the same row (clamped), ↑/↓ within a step, ↑ from the top task to the step's label.
  const loopKeys = loopTasks.map((t) => taskKey("fix_loop", t.id));
  const chipRows = shown ? shown.view.rows.map((r) => r.chips.map((c) => chipKey(shown.step, shown.task, c.key))).filter((r) => r.length) : [];
  const move = (key: string, dir: string): string | undefined => {
    const { step, task } = parse(key);
    // An open task's chips: ←/→ along the row, ↑/↓ to the row above or below (clamped), ↑ from the first row to the task.
    if (key.startsWith("c:") && shown) {
      const r = chipRows.findIndex((row) => row.includes(key));
      const j = chipRows[r]?.indexOf(key) ?? -1;
      if (dir === "ArrowLeft") return chipRows[r][Math.max(0, j - 1)];
      if (dir === "ArrowRight") return chipRows[r][Math.min(chipRows[r].length - 1, j + 1)];
      if (dir === "ArrowUp") return r > 0 ? chipRows[r - 1][Math.min(j, chipRows[r - 1].length - 1)] : taskKey(shown.step, shown.task);
      if (dir === "ArrowDown") return r < chipRows.length - 1 ? chipRows[r + 1][Math.min(j, chipRows[r + 1].length - 1)] : undefined;
      return;
    }
    if (shown && phase.mounted && key === taskKey(shown.step, shown.task) && dir === "ArrowDown" && chipRows.length) return chipRows[0][0];
    // The loop's tasks sit on the arc, left to right: ←/→ between them, ↑ back to the last step.
    if (step === "fix_loop") {
      const j = loopKeys.indexOf(key);
      if (dir === "ArrowLeft") return loopKeys[Math.max(0, j - 1)];
      if (dir === "ArrowRight") return loopKeys[Math.min(loopKeys.length - 1, j + 1)];
      const end = steps.at(-1);
      if (dir === "ArrowUp" && end) return end.tasks.length ? taskKey(end.id, end.tasks.at(-1)!.id) : labelKey(end.id);
      return;
    }
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
    if (dir === "ArrowDown") return i >= 0 && i + 1 >= steps[k].tasks.length && loopKeys.length ? loopKeys[0] : at(k, i + 1);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); onEscape?.(); return; }
    const key = roving.active;
    if (!key || !(e.target as Element).closest(".graph-node, .step-label, .scope-chip, .scope-task")) return;
    if (e.key.startsWith("Arrow")) { e.preventDefault(); roving.go(move(key, e.key)); }
    else if (e.key === "Enter") { e.preventDefault(); (e.metaKey || e.ctrlKey ? onExpand : onOpen)?.(parse(key)); }
  };
  const onClick = (e: MouseEvent) => {
    if (!(e.target as Element).closest("button, .step-frame, .scope-frame")) onBackground?.();
  };
  // The prototype moves a path by transitioning the CSS `d` (Chrome); elsewhere it snaps to its new place.
  const glide = (d: string) => (phase.glide ? ({ d: `path("${d}")` } as CSSProperties) : undefined);
  const { cam } = camera;
  const loopShape = loop && loopArc(lay, loop.label);
  const judged = loopTasks.length > 0 && loopTasks.at(-1)!.id === "judge";
  const slots = loopSlots(lay, loopTasks.length - (judged ? 1 : 0), judged);
  const sb = side && sideBranch(lay);
  const isSel = (s: NodeSel) => selected?.step === s.step && selected?.task === s.task && !selected?.scope;
  const last = lay.cols[lay.cols.length - 1];

  const seam = (key: string, x: number, y: number, title: string, fire: (el: HTMLElement) => void) => (
    <button key={key} type="button" className="seam is-node" {...tip(title)} style={{ left: x - 10, top: y - 10 }} onClick={(e) => fire(e.currentTarget)}><span aria-hidden="true">+</span></button>
  );
  // `away`: the task is open as a frame, so its box fades; the frame's own title holds its place in the keys.
  const taskButton = (s: NodeSel, t: GraphItem, x: number, y: number, kind: string, extra = "", away?: boolean) => {
    const key = taskKey(s.step, t.id), sel = isSel(s);
    return (
      <button
        key={key}
        ref={away === undefined ? roving.ref(key) : undefined}
        type="button"
        tabIndex={away === undefined ? roving.tabIndex(key) : -1}
        // A fix loop's task is selected as `<step>.<task>` but named by the task.
        aria-label={accessibleName(s.step === "fix_loop" ? { ...t, id: t.label ?? t.id } : t, kind)}
        aria-pressed={sel}
        aria-hidden={away || undefined}
        className={`graph-node is-task${sel || t.state === "current" ? " is-bold" : ""}${t.state === "todo" ? " is-todo" : ""}${t.state === "esc" || t.state === "amber" ? " is-warn" : ""}${t.pending ? " is-pending" : ""}${away ? " is-away" : ""}${extra}`}
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
    <div role="group" aria-label={name} className={`canvas${calm ? " is-calm" : ""}`} data-pan {...camera.bind} onClick={onClick} onKeyDown={onKeyDown} onFocus={() => (inside.current = true)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) inside.current = false; }}>
      <div className={`canvas-world${phase.glide ? " is-glide" : ""}`} style={{ width: lay.W, height: lay.H, transform: `translate(${cam.tx}px, ${cam.ty}px) scale(${cam.s})` }}>
        {steps.map((st, k) => {
          const f = lay.cols[k].frame;
          const cls = `step-frame${selected?.step === st.id && !selected.task ? " is-sel" : ""}${st.mark ? ` mark-${st.mark}` : st.prob ? " is-prob" : ""}`;
          return f && <div key={`f${st.id}`} aria-hidden="true" className={cls} style={{ left: f.x, top: f.y, width: f.w, height: f.h }} onClick={() => onSelect?.({ step: st.id })} />;
        })}
        <svg className="canvas-svg" width={lay.W} height={lay.H} aria-hidden="true">
          {edges.map((e) => <path key={e.key} d={e.d} style={glide(e.d)} className={e.todo ? "edge is-todo" : "edge"} />)}
          {dots.map((d, i) => <circle key={`j${i}`} cx={d.x} cy={d.y} r={3} style={phase.glide ? ({ cx: d.x, cy: d.y } as CSSProperties) : undefined} className="dot" />)}
          {sb && <path d={sb.d} style={glide(sb.d)} className="edge is-side" />}
          {loopShape && (
            <g className={`arc tone-${loop.tone ?? "idle"}`}>
              <path d={loopShape.d} style={glide(loopShape.d)} className="is-dashed" />
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
            ...st.tasks.map((t, i) => taskButton({ step: st.id, task: t.id }, t, cx, ys[i], `${t.taskKind ?? "agent"} task`, "", open && phase.mounted && k === open.step && i === open.row ? phase.layout || phase.content : undefined)),
            !st.tasks.length && st.slot && (
              <button key={`slot${st.id}`} type="button" className="graph-node is-task is-slot" style={{ left: cx - G.COL / 2, top: ys[0] - G.BOX / 2, width: G.COL }} onClick={(e) => onSlot?.(st.id, e.currentTarget)}>
                <NodeGlyph kind="slot" size="md" />
                <span className="graph-label">{st.slot.label ?? "add a task"}</span>
              </button>
            ),
            st.seamBelow && seam(`sb${st.id}`, cx, ys[ys.length - 1] + G.BOX / 2 + 56, "Add a parallel task", (el) => onSeam?.("below", st.id, el)),
            st.seamBefore && seam(`sf${st.id}`, k === 0 ? (G.startX + 18 + cx - G.BOX / 2) / 2 : (lay.cols[k - 1].right + lay.cols[k].left) / 2, lay.TY, "Add a step here", (el) => onSeam?.("before", k, el)),
          ];
        })}
        {loopTasks.map((t, i) => taskButton({ step: "fix_loop", task: t.id }, t, slots[i].x, slots[i].y, `fix-loop ${t.id === "judge" ? "judge" : "repair"} ${t.taskKind ?? "agent"} task`, t.faded ? " is-faded" : ""))}
        {shown && open && phase.mounted && (
          <ScopeFrame
            view={shown.view}
            width={open.w}
            step={shown.step}
            task={shown.task}
            rect={phase.layout ? wide.cols[open.step].open! : { x: closed.cx[open.step] - G.BOX / 2, y: closed.cols[open.step].ys[open.row] - G.BOX / 2, w: G.BOX, h: G.BOX }}
            on={phase.content}
            full={phase.layout}
            out={!expand && !phase.layout}
            selectedScope={expand?.scope}
            taskKey={taskKey(shown.step, shown.task)}
            rove={{ ref: roving.ref, tabIndex: roving.tabIndex, go: roving.go, onFocus: (key) => { roving.go(key); if (quiet.current) return; const f = wide.cols[open.step].open!; camera.reveal({ x0: f.x, x1: f.x + f.w, y0: f.y, y1: f.y + f.h }); } }}
            onTask={() => onSelect?.({ step: shown.step, task: shown.task })}
            onScope={(key) => onScope?.(key)}
            onClose={() => onCollapse?.()}
          />
        )}
        {sb && side && taskButton({ step: "escalation", task: side.id }, { ...side, state: side.state ?? "esc" }, sb.x, sb.y, "escalation task", " is-side")}
        {onFailure && <span className="node-footer" style={{ left: G.startX, top: lay.footerY }}>on failure · {onFailure}</span>}
        {seamAfter && seam("after", last ? (last.right + lay.endX) / 2 : (G.startX + 18 + lay.endX) / 2, lay.TY, "Add a step here", (el) => onSeam?.("after", steps.length, el))}
      </div>
      <div className="canvas-zoom" style={{ right: reserve + 12 }}>
        <ZoomControls scale={cam.s} mode={camera.mode} onIn={camera.zoomIn} onOut={camera.zoomOut} onReset={camera.reset} onFit={camera.fit} fitLabel="Fit the node" />
        {rounds && onRound && <RoundPicker rounds={rounds} onPick={onRound} />}
      </div>
    </div>
  );
}
