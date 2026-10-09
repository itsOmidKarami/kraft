import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from "react";
import { NodeGlyph } from "./NodeGlyph";
import { G, LOOP_FRAME, loopArc, loopCols, loopColumn, loopFork, loopSlots, nodeEdges, nodeLayout, sideBranch, type NodeStep } from "./nodeLayout";
import { RoundPicker } from "./RoundPicker";
import { chipKey, ScopeFrame } from "./ScopeFrame";
import { frameHeight, frameWidth, type ScopesView } from "../item/scopeView";
import type { Rounds } from "../item/nodeGraph";
import { accessibleName, breakable, type GlyphState, type GraphItem } from "./types";
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
  loop?: { tone?: "idle" | "active" | "red"; label?: string; /** The repair tasks, then the judge, drawn on the arc. A repair's id is `<step>.<task>`: a step of several is one box there, which opens into them. */ tasks?: GraphItem[] };
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
/** A fix-loop step of several tasks: one stop, selected as `fix_loop` and the step's own id, where its tasks are `<step>.<task>`. */
const loopStepKey = (s: string) => taskKey("fix_loop", s);
/** That box's look: the worst of its tasks, running while any runs, with that task's own word for why it waits
 *  ("needs you", "paused") so the box is not read as running when nothing runs. Some done and the rest not begun
 *  is under way too, and says how far. */
function stepLook(tasks: GraphItem[]): Pick<GraphItem, "state" | "running" | "paused" | "faded"> & { wait?: string } {
  const stateOf = (t: GraphItem) => t.state ?? "todo";
  const worst = (["failed", "current", "amber"] as GlyphState[]).map((s) => tasks.find((t) => stateOf(t) === s)).find(Boolean);
  const done = tasks.filter((t) => stateOf(t) === "done").length;
  const running = tasks.some((t) => t.running), faded = tasks.every((t) => t.faded);
  if (worst) return { state: worst.state, running, paused: !running && tasks.some((t) => t.paused), faded, wait: worst.meta };
  if (done === tasks.length) return { state: "done", faded };
  return done ? { state: "current", faded, wait: `${done} of ${tasks.length} done` } : { state: "todo", faded };
}
const labelKey = (s: string) => `l:${s}`;
const parse = (key: string): NodeSel => {
  const [step, task, ...rest] = key.slice(2).split("/");
  return key.startsWith("c:") ? { step, task, scope: rest.join("/") } : key.startsWith("t:") ? { step, task } : { step };
};

/** A node's inside: steps in order, parallel tasks as rows (NodeGraph.dc.html). */
export function NodeGraph({ name, steps, selected, side, loop, rounds, onRound, expand, onScope, onCollapse, onFailure, seamAfter, reserve = 0, onSelect, onOpen, onExpand, onEscape, onBackground, onSlot, onSeam }: Props) {
  const calm = useReducedMotion();
  // What the arc carries: a loop step's tasks run together, so a step of several is one box. It is open as a frame
  // of them while it, or one of them, is the selection, as a changed-test-scope task is; `shut` folds it where it
  // stands, until the selection moves or it is picked again.
  const arcCols = loopCols(loop?.tasks ?? []);
  const several = (step: string | undefined) => (step === undefined ? undefined : arcCols.find((c) => c.step === step && c.tasks.length > 1));
  const selStep = selected?.step === "fix_loop" ? arcCols.find((c) => c.tasks.length > 1 && (c.step === selected.task || c.tasks.some((t) => t.id === selected.task)))?.step : undefined;
  const [shut, setShut] = useState<string>();
  useEffect(() => setShut(undefined), [selected?.step, selected?.task]);
  const loopOpen = selStep !== shut ? selStep : undefined;
  const loopKept = useRef(loopOpen);
  if (loopOpen) loopKept.current = loopOpen;
  const loopPhase = useExpand(!!loopOpen, calm);
  // Its tasks show while the canvas is laid out for it; closing, the frame shrinks back onto the box first.
  const openCol = loopPhase.layout ? several(loopKept.current) : undefined;
  // The focus follows the step between its box and its frame's head when the keys or a click moved it, not when a selection elsewhere did.
  const follow = useRef(false);
  const shutLoop = () => { follow.current = true; setShut(selStep); };
  // Picking a step that is already open moves nothing, so it arms nothing; a second click of a double-click must not disarm the first.
  const pickLoop = (step: string, how = onSelect) => { follow.current ||= loopOpen !== step; setShut(undefined); how?.({ step: "fix_loop", task: step }); };
  // The frame stays in the page while it closes, which takes the view it was drawn from with it.
  const kept = useRef(expand);
  if (expand) kept.current = expand;
  const expanding = useExpand(!!expand, calm);
  // Either frame moving holds the camera and glides the rest.
  const phase = { ...expanding, glide: expanding.glide || loopPhase.glide };
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
  // A loop with a parallel step is a lane, open or not: its stops on a level line, as steps are on the axis.
  const laid = { side: !!side, loop: !!loop, loopTasks: arcCols.length, loopLane: arcCols.some((c) => c.tasks.length > 1), loopOpen: openCol?.tasks.length ?? 0, footer: !!onFailure };
  const closed = useMemo(() => nodeLayout(steps, laid), [steps, side, loop, arcCols.length, laid.loopLane, laid.loopOpen, onFailure]); // eslint-disable-line react-hooks/exhaustive-deps
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

  // A loop step's box gives way to its frame, and the frame to it: the focus stays on the step, the box or the
  // frame's head, without panning a camera that is still settling.
  useEffect(() => {
    if (!follow.current) return;
    follow.current = false;
    quiet.current = true;
    roving.go(loopKept.current && loopStepKey(loopKept.current));
    quiet.current = false;
    // The step too: another opened over an open one changes no phase.
  }, [loopPhase.layout, openCol?.step]); // eslint-disable-line react-hooks/exhaustive-deps

  // ←/→ to the neighbouring step at the same row (clamped), ↑/↓ within a step, ↑ from the top task to the step's label.
  // A step of several is its own stop, with its tasks under it while it is open.
  const loopKeys = arcCols.map((col) => [...(col.tasks.length > 1 ? [loopStepKey(col.step!)] : []), ...(col.tasks.length === 1 || col === openCol ? col.tasks.map((t) => taskKey("fix_loop", t.id)) : [])]);
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
    // The loop's stops sit on the arc, left to right: ←/→ between them at the same row (clamped), ↑/↓ within an
    // open step, ↑ from a stop's top back to the last step.
    const c = loopKeys.findIndex((col) => col.includes(key));
    if (c >= 0) {
      const r = loopKeys[c].indexOf(key);
      const at = (cc: number, row: number) => { const col = loopKeys[Math.max(0, Math.min(loopKeys.length - 1, cc))]; return col?.[Math.min(row, col.length - 1)]; };
      if (dir === "ArrowLeft") return at(c - 1, r);
      if (dir === "ArrowRight") return at(c + 1, r);
      if (dir === "ArrowDown") return at(c, r + 1);
      if (dir === "ArrowUp" && r > 0) return at(c, r - 1);
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
    if (dir === "ArrowDown") return i >= 0 && i + 1 >= steps[k].tasks.length && loopKeys.length ? loopKeys[0][0] : at(k, i + 1);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    // Esc steps back one: from inside an open loop step, to its box; anywhere else it is the page's own.
    if (e.key === "Escape") { e.preventDefault(); if (loopOpen && (e.target as Element).closest(".loop-frame, .is-framed")) shutLoop(); else onEscape?.(); return; }
    const key = roving.active;
    if (!key || !(e.target as Element).closest(".graph-node, .step-label, .scope-chip, .scope-task")) return;
    if (e.key.startsWith("Arrow")) { e.preventDefault(); roving.go(move(key, e.key)); }
    else if (e.key === "Enter") {
      e.preventDefault();
      // A scope chip opens its scope, as Space and a click do; a loop step of several unfolds too.
      const at = parse(key);
      if (at.scope && !(e.metaKey || e.ctrlKey)) onScope?.(at.scope);
      else if (at.step === "fix_loop" && several(at.task)) pickLoop(at.task!, e.metaKey || e.ctrlKey ? onExpand : onOpen);
      else (e.metaKey || e.ctrlKey ? onExpand : onOpen)?.(at);
    }
  };
  const onClick = (e: MouseEvent) => {
    if (!(e.target as Element).closest("button, .step-frame, .scope-frame")) onBackground?.();
  };
  // The prototype moves a path by transitioning the CSS `d` (Chrome); elsewhere it snaps to its new place.
  const glide = (d: string) => (phase.glide ? ({ d: `path("${d}")` } as CSSProperties) : undefined);
  const { cam } = camera;
  const judged = arcCols.at(-1)?.tasks[0].id === "judge";
  const slots = loopSlots(lay, arcCols.length - (judged ? 1 : 0), judged);
  const openAt = openCol ? arcCols.indexOf(openCol) : -1;
  const openFrame = openAt >= 0 ? loopColumn(slots[openAt], openCol!.tasks.length) : undefined;
  // Inside the open frame the loop's line gives way to a fork: a branch to each task, joined again on the far side.
  const fork = openFrame && loopFork(slots[openAt], openFrame);
  const clip = useId();
  // The label sits at the arc's middle. More than a pair puts a task there, and an open step's frame reaches it:
  // it then goes midway between the last two stops' edges, or just right of a frame that is alone.
  const edge = (c: number) => (c === openAt ? LOOP_FRAME.w / 2 : G.BOX / 2), n = slots.length;
  const labelX = n > 2 || (n === 2 && openFrame) ? (slots[n - 2].x + edge(n - 2) + slots[n - 1].x - edge(n - 1)) / 2 : n === 1 && openFrame ? slots[0].x + LOOP_FRAME.w / 2 + 45 : undefined;
  const loopShape = loop && loopArc(lay, loop.label, labelX);
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
        onFocus={() => { roving.go(key); if (!quiet.current) camera.reveal({ x0: x - G.COL / 2, x1: x + G.COL / 2, y0: y - G.BOX / 2, y1: y + G.BOX / 2 + 36 }); }}
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
              {/* The loop runs from one join to the other only as the fork: its own line is cut between them. */}
              {fork && <clipPath id={clip}><path clipRule="evenodd" d={`M-1e5 -1e5H1e5V1e5H-1e5Z M${fork.cut[0]} -1e5H${fork.cut[1]}V1e5H${fork.cut[0]}Z`} /></clipPath>}
              <path d={loopShape.d} style={glide(loopShape.d)} className="is-dashed" clipPath={fork ? `url(#${clip})` : undefined} />
              <path d={loopShape.arrow} />
              {fork && (
                <g className="arc-fork">
                  {fork.edges.map((e) => <path key={e.key} d={e.d} className="is-dashed" />)}
                  {fork.dots.map((d, i) => <circle key={i} cx={d.x} cy={d.y} r={3} />)}
                </g>
              )}
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
        {loopPhase.mounted && several(loopKept.current) && (() => {
          // The frame grows from the step's box and shrinks back onto it, as an open task's does.
          const col = several(loopKept.current)!, at = slots[arcCols.indexOf(col)], key = loopStepKey(col.step!);
          const f = openFrame?.frame ?? { x: at.x - G.BOX / 2, y: at.y - G.BOX / 2, w: G.BOX, h: G.BOX };
          // The head is the step's box, expanded: it selects the step and says what the box says. It is the step's
          // stop only while the box is away: the two share a key.
          const head = col === openCol, look = stepLook(col.tasks);
          return (
            <div role="group" aria-label={`${col.step}, tasks in parallel`} className={`scope-frame loop-frame${loopPhase.content ? " is-on" : ""}${!loopOpen && !loopPhase.layout ? " is-out" : ""}`} style={{ left: f.x, top: f.y, width: f.w, height: f.h }}>
              <div className="scope-head" style={{ width: LOOP_FRAME.w }}>
                <button ref={head ? roving.ref(key) : undefined} type="button" tabIndex={head ? roving.tabIndex(key) : -1} className="scope-task loop-step" aria-expanded aria-pressed={isSel({ step: "fix_loop", task: col.step })} aria-label={accessibleName({ id: col.step!, state: look.state, running: look.running, wait: look.wait }, `fix-loop step of ${col.tasks.length} parallel tasks`)} onFocus={() => { roving.go(key); if (!quiet.current) camera.reveal({ x0: f.x, x1: f.x + f.w, y0: f.y, y1: f.y + f.h }); }} onClick={() => pickLoop(col.step!)}>{col.step}</button>
                <span className="scope-sub">in parallel</span>
                {/* Out of the Tab order, as the scope frame's: Esc closes it from the keys. */}
                <button type="button" tabIndex={-1} className="scope-close" {...tip("Close")} onClick={shutLoop}>✕</button>
              </div>
            </div>
          );
        })()}
        {arcCols.map((col, c) => {
          const kind = (t: GraphItem) => `fix-loop ${t.id === "judge" ? "judge" : "repair"} ${t.taskKind ?? "agent"} task`;
          if (col === openCol) return col.tasks.map((t, j) => taskButton({ step: "fix_loop", task: t.id }, t, slots[c].x, openFrame!.ys[j], kind(t), " is-framed"));
          if (col.tasks.length === 1) return taskButton({ step: "fix_loop", task: col.tasks[0].id }, col.tasks[0], slots[c].x, slots[c].y, kind(col.tasks[0]), col.tasks[0].faded ? " is-faded" : "");
          // A step of several, closed: one box. Selecting it opens it into them.
          const key = loopStepKey(col.step!), look = stepLook(col.tasks), { x, y } = slots[c], n = col.tasks.length, sel = isSel({ step: "fix_loop", task: col.step });
          return (
            <button
              key={key}
              ref={roving.ref(key)}
              type="button"
              tabIndex={roving.tabIndex(key)}
              aria-expanded={false}
              aria-pressed={sel}
              aria-label={accessibleName({ id: col.step!, state: look.state, running: look.running, wait: look.wait }, `fix-loop step of ${n} parallel tasks`)}
              className={`graph-node is-task${sel || look.state === "current" ? " is-bold" : ""}${look.state === "todo" ? " is-todo" : ""}${look.state === "amber" ? " is-warn" : ""}${look.faded ? " is-faded" : ""}`}
              style={{ left: x - G.COL / 2, top: y - G.BOX / 2, width: G.COL }}
              onFocus={() => { roving.go(key); if (!quiet.current) camera.reveal({ x0: x - G.COL / 2, x1: x + G.COL / 2, y0: y - G.BOX / 2, y1: y + G.BOX / 2 + 36 }); }}
              onClick={() => pickLoop(col.step!)}
              onDoubleClick={() => onExpand?.({ step: "fix_loop", task: col.step })}
            >
              <NodeGlyph icon="layers" state={look.state} running={look.running} paused={look.paused} size="md" sel={selStep === col.step} />
              <span className="graph-label" style={{ maxWidth: G.COL - 6 }}>{breakable(col.step!)}</span>
              <span className="graph-meta tone-muted">{n} in parallel</span>
            </button>
          );
        })}
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
